mod rails;
mod avatar;
#[cfg(test)]
mod collision_checks;
use serde::{Deserialize, Serialize};
use skate_host::bridge::{Controls, Pose, Session, PreparedCollision};
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    time::Duration,
    thread::JoinHandle,
    sync::mpsc,
};

type Triangle = [[f32; 3]; 3];
const MAX_MESSAGE: usize = 16 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Activate { triangles: Vec<Triangle>, rails: Vec<Vec<[f32; 3]>>, spawn: [f32; 3], heading: f32,
        #[serde(default = "default_aspect")] aspect: f32 },
    Collision { triangles: Vec<Triangle>, rails: Vec<Vec<[f32; 3]>> },
    Step { buttons: u16, triggers: [u8; 2], left: [i16; 2], right: [i16; 2], ticks: u8,
        #[serde(default = "default_aspect")] aspect: f32 },
    ActivatePacked { count: usize, spawn: [f32; 3], heading: f32,
        #[serde(default = "default_aspect")] aspect: f32 },
    CollisionPacked { count: usize },
    Resume { spawn: [f32; 3], heading: f32,
        #[serde(default = "default_aspect")] aspect: f32,
        #[serde(default)] velocity: [f32; 3] },
    Suspend,
    #[serde(skip)]
    Invalid { error: String },
}

fn default_aspect() -> f32 { 16.0 / 9.0 }
fn validate_aspect(aspect: f32) -> Result<(), String> {
    if !aspect.is_finite() || !(0.25..=8.0).contains(&aspect) {
        return Err("Camera aspect must be finite and in 0.25..8".into());
    }
    Ok(())
}

#[derive(Serialize)]
struct Frame {
    ok: bool,
    root: [f32; 16],
    bones: Vec<[f32; 16]>,
    names: Vec<String>,
    camera: Option<Camera>,
    velocity: [f32; 3],
    tick: u64,
    state: String,
    line_points: f32,
    line_trick: String,
    period: f32,
}

#[derive(Serialize)]
struct Camera { position: [f32; 3], basis: [f32; 9], fov: f32 }

fn frame(p: Pose, period: f32) -> Result<Frame, String> {
    if !p.root.is_finite() || !p.velocity.is_finite() || p.bones.iter().any(|b| !b.is_finite())
        || !p.line_points.is_finite() || !period.is_finite() || period <= 0.0
        || p.camera.as_ref().is_some_and(|(p,b,f)| !p.is_finite() || !b.is_finite() || !f.is_finite())
    {
        return Err("Simulation produced a non-finite pose".into());
    }
    Ok(Frame {
        ok: true, root: p.root.to_cols_array(),
        bones: p.bones.iter().map(|b| b.to_cols_array()).collect(),
        names: p.names,
        camera: p.camera.map(|(position,basis,fov)| Camera {
            position: position.to_array(), basis: basis.to_cols_array(), fov,
        }),
        velocity: p.velocity.to_array(), tick: p.tick, state: p.state,
        line_points: p.line_points, line_trick: p.line_trick, period,
    })
}

fn validate_geometry(triangles: &[Triangle], rails: &[Vec<[f32; 3]>]) -> Result<(), String> {
    if triangles.is_empty() || triangles.len() > 200_000 || rails.len() > 20_000 {
        return Err("Expected 1..200000 collision triangles and at most 20000 rails".into());
    }
    let mut grid_cells = 0u64;
    for (index,triangle) in triangles.iter().enumerate() {
        if triangle.iter().flatten().any(|v| !v.is_finite() || v.abs() > 10_000.0) {
            return Err("Invalid collision coordinate".into());
        }
        let span = |axis: usize| {
            let min = triangle.iter().map(|p|p[axis]).fold(f32::INFINITY,f32::min);
            let max = triangle.iter().map(|p|p[axis]).fold(f32::NEG_INFINITY,f32::max);
            ((max-min)/1.6256).ceil() as u64 + 2
        };
        grid_cells += span(0)*span(2);
        if grid_cells > 2_000_000 { return Err("Collision spans too many rail probe cells".into()); }
        let a = std::array::from_fn::<_,3,_>(|i| triangle[1][i] - triangle[0][i]);
        let b = std::array::from_fn::<_,3,_>(|i| triangle[2][i] - triangle[0][i]);
        let cross = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
        if cross.iter().map(|v| v*v).sum::<f32>() < 1e-10 {
            return Err(format!("Degenerate collision triangle #{index}"));
        }
    }
    if rails.iter().any(|r| r.len()<2 || r.len()>4096 ||
        r.iter().flatten().any(|v| !v.is_finite() || v.abs()>10_000.0)) {
        return Err("Invalid grind rail".into());
    }
    Ok(())
}

fn grind_rails(triangles: &[Triangle]) -> Vec<Vec<[f32; 3]>> {
    use bevy::math::Vec3;
    const INCH: f32 = 0.0254;
    let z_up: Vec<[Vec3; 3]> = triangles.iter().map(|t| t.map(|p| Vec3::new(p[0], -p[2], p[1])/INCH)).collect();
    let (found, census) = rails::find(&z_up);
    eprintln!("Grind rails: {} candidates, {} lips, {} runs, {} rails",census.candidates,census.lips,census.runs,census.rails);
    found.into_iter().take(20_000).map(|r| r.into_iter().map(|p| [p.x*INCH,p.z*INCH,-p.y*INCH]).collect()).collect()
}

type CollisionJob = JoinHandle<Result<PreparedCollision, String>>;

#[derive(Default)]
struct BridgeState {
    session: Option<Session>,
    collision: Option<CollisionJob>,
    active: bool,
    rest: Option<Vec<[f32; 16]>>,
}

fn apply(request: Request, state: &mut BridgeState, root: &Path) -> Result<serde_json::Value, String> {
    let BridgeState {session,collision,active,rest}=state;
    if collision.as_ref().is_some_and(|job| job.is_finished()) {
        let prepared=collision.take().unwrap().join().map_err(|_|"Collision worker panicked")??;
        session.as_mut().ok_or("Collision session was closed")?.install_collision(prepared)?;
    }
    match request {
        Request::Invalid {error} => Err(error),
        Request::Activate { triangles, rails, spawn, heading, aspect } => {
            if let Some(job)=collision.take() { let _=job.join(); }
            validate_geometry(&triangles, &rails)?;
            validate_aspect(aspect)?;
            if spawn.iter().any(|v| !v.is_finite() || v.abs()>10_000.0) || !heading.is_finite() {
                return Err("Invalid spawn".into());
            }
            let rails = if rails.is_empty() { grind_rails(&triangles) } else { rails };
            if let Some(s)=session {
                let prepared=s.collision_builder().build(triangles,rails)?;
                s.install_collision(prepared)?;
            } else {
                *session=Some(Session::new(root,triangles,rails,spawn,heading)?);
            }
            let s=session.as_mut().unwrap();
            s.set_aspect_ratio(aspect);
            s.set_respawn_in_place(true);
            let p=s.activate(spawn,heading)?;
            if rest.is_none() { *rest=Some(avatar::bind_pose(root,&p.names)?); }
            let mut result=serde_json::to_value(frame(p,s.period())?).map_err(|e|e.to_string())?;
            result["rest"]=serde_json::json!(rest);
            *active=true;
            Ok(result)
        }
        Request::Resume {spawn,heading,aspect,velocity} => {
            validate_aspect(aspect)?;
            if spawn.iter().any(|v| !v.is_finite() || v.abs()>10_000.0) || !heading.is_finite() ||
                velocity.iter().any(|v| !v.is_finite() || v.abs()>1000.) {
                return Err("Invalid spawn".into());
            }
            let s=session.as_mut().ok_or("Activate a session first")?;
            if rest.is_none() {return Err("Activate a session first".into());}
            s.set_aspect_ratio(aspect);
            s.activate(spawn,heading)?;
            let baseline=s.pose().velocity.to_array();
            s.add_velocity(std::array::from_fn(|i|velocity[i]-baseline[i]));
            let p=s.pose();
            let mut result=serde_json::to_value(frame(p,s.period())?).map_err(|e|e.to_string())?;
            result["rest"]=serde_json::json!(rest);
            *active=true;
            Ok(result)
        }
        Request::Suspend => {
            if let Some(s) = session { s.suspend_input(); }
            *active=false;
            Ok(serde_json::json!({"ok":true}))
        }
        request => {
            let s = session.as_mut().ok_or("Activate a session first")?;
            match request {
                Request::Collision {triangles,rails} => {
                    if collision.is_some() { return Err("A collision update is already pending".into()); }
                    validate_geometry(&triangles,&rails)?;
                    let builder=s.collision_builder();
                    // Build the next world while ticks keep using the current one.
                    *collision=Some(std::thread::Builder::new().name("collision".into()).stack_size(32*1024*1024)
                        .spawn(move || {
                            let rails=if rails.is_empty() { grind_rails(&triangles) } else { rails };
                            builder.build(triangles,rails)
                        }).map_err(|e|e.to_string())?);
                }
                Request::Step {buttons,triggers,left,right,ticks,aspect} => {
                    if !*active {return Err("Activate or resume a session first".into());}
                    if !(1..=8).contains(&ticks) { return Err("ticks must be 1..8".into()); }
                    validate_aspect(aspect)?;
                    s.set_aspect_ratio(aspect);
                    for _ in 0..ticks { s.tick(Controls {buttons,triggers,left,right})?; }
                }
                _ => unreachable!(),
            }
            let mut value=serde_json::to_value(frame(s.pose(),s.period())?).map_err(|e|e.to_string())?;
            value["collision_pending"]=serde_json::json!(collision.is_some());
            Ok(value)
        }
    }
}

type Command=(Request,mpsc::SyncSender<serde_json::Value>);

fn read_request(reader: &mut BufReader<TcpStream>) -> Result<Option<Request>, String> {
    let mut bytes=Vec::new();
    let n=reader.take((MAX_MESSAGE+1) as u64).read_until(b'\n',&mut bytes).map_err(|e|e.to_string())?;
    if n==0 {return Ok(None);}
    if n>MAX_MESSAGE || bytes.last()!=Some(&b'\n') {return Err("Oversized or incomplete message".into());}
    let request=match serde_json::from_slice::<Request>(&bytes) {
        Ok(request) => request,
        Err(error) => return Ok(Some(Request::Invalid {error:error.to_string()})),
    };
    let count=match &request {
        Request::ActivatePacked {count,..} | Request::CollisionPacked {count} => *count,
        _ => return Ok(Some(request)),
    };
    if !(1..=200_000).contains(&count) {return Err("Invalid packed triangle count".into());}
    // Fixed-size little-endian float triples follow the bounded JSON header; no trailing delimiter.
    let mut packed=vec![0;count*36];
    reader.get_ref().set_read_timeout(Some(Duration::from_secs(30))).map_err(|e|e.to_string())?;
    reader.read_exact(&mut packed).map_err(|e|e.to_string())?;
    reader.get_ref().set_read_timeout(None).map_err(|e|e.to_string())?;
    let triangles=packed.chunks_exact(36).map(|triangle|std::array::from_fn(|vertex|
        std::array::from_fn(|axis| {
            let i=(vertex*3+axis)*4;
            f32::from_le_bytes(triangle[i..i+4].try_into().unwrap())
        }))).collect();
    Ok(Some(match request {
        Request::ActivatePacked {spawn,heading,aspect,..} => Request::Activate {triangles,rails:vec![],spawn,heading,aspect},
        Request::CollisionPacked {..} => Request::Collision {triangles,rails:vec![]},
        _ => unreachable!(),
    }))
}

fn serve(stream: TcpStream, commands: mpsc::Sender<Command>) -> Result<(), Box<dyn std::error::Error>> {
    stream.set_nodelay(true)?;
    stream.set_read_timeout(None)?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;
    let mut reader=BufReader::new(stream.try_clone()?);
    let mut writer=stream;
    loop {
        let request=match read_request(&mut reader) {
            Ok(Some(request)) => request,
            Ok(None) => return Ok(()),
            Err(error) => {
                // A bad header cannot safely select where its next binary payload ends.
                let (reply,received)=mpsc::sync_channel(1);
                commands.send((Request::Invalid {error},reply))?;
                serde_json::to_writer(&mut writer,&received.recv()?)?;
                writer.write_all(b"\n")?; writer.flush()?;
                return Ok(());
            }
        };
        let (reply,received)=mpsc::sync_channel(1);
        commands.send((request,reply))?;
        serde_json::to_writer(&mut writer,&received.recv()?)?;
        writer.write_all(b"\n")?; writer.flush()?;
    }
}

fn floor() -> Vec<Triangle> {
    vec![
        [[-100.,0.,-100.],[-100.,0.,100.],[100.,0.,100.]],
        [[-100.,0.,-100.],[100.,0.,100.],[100.,0.,-100.]],
    ]
}

fn smoke(root: &Path) -> Result<(), String> {
    let mut s = Session::new(root,floor(),vec![],[0.,0.3,0.],0.)?;
    s.set_respawn_in_place(true);
    let start = frame(s.activate([0.,0.3,0.],0.)?,s.period())?;
    for _ in 0..120 { s.tick(Controls::default())?; frame(s.pose(),s.period())?; }
    let settled = frame(s.pose(),s.period())?;
    for _ in 0..120 { s.tick(Controls {buttons:0x1000,..Controls::default()})?; frame(s.pose(),s.period())?; }
    let end = frame(s.pose(),s.period())?;
    let distance=((end.root[12]-settled.root[12]).powi(2)+(end.root[14]-settled.root[14]).powi(2)).sqrt();
    if distance < 0.1 || end.root[13].abs() > 5.0 || settled.root[13].abs()>5.0 {
        return Err(format!("Smoke test failed: distance={distance}, settled_y={}, final_y={}",settled.root[13],end.root[13]));
    }
    println!("{}",serde_json::json!({
        "ok":true, "ticks":end.tick, "bones":end.bones.len(),
        "period":s.period(), "distance":distance,
        "initial_position":&start.root[12..15], "settled_position":&settled.root[12..15],
        "final_position":&end.root[12..15], "velocity":end.velocity, "state":end.state,
    }));
    Ok(())
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args=std::env::args().skip(1);
    let mut assets=PathBuf::from("data/ps3-assets");
    let mut address="127.0.0.1:31333".to_string();
    let mut test=false;
    while let Some(arg)=args.next() {
        match arg.as_str() {
            "--assets" => assets=PathBuf::from(args.next().ok_or("--assets needs a directory")?),
            "--listen" => address=args.next().ok_or("--listen needs an address")?,
            "--smoke-test" => test=true,
            _ => return Err(format!("Unknown argument {arg}").into()),
        }
    }
    if test { return smoke(&assets).map_err(Into::into); }
    let socket: std::net::SocketAddr=address.parse()?;
    if !socket.ip().is_loopback() { return Err("The bridge only binds to loopback".into()); }
    let listener=TcpListener::bind(socket)?;
    eprintln!("Skate bridge listening on {}",listener.local_addr()?);
    let (commands,received)=mpsc::channel::<Command>();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            match stream {
                Ok(stream) => {
                    let commands=commands.clone();
                    std::thread::spawn(move || {
                        if let Err(error)=serve(stream,commands) {eprintln!("Client disconnected: {error}");}
                    });
                }
                Err(error) => eprintln!("Accept failed: {error}"),
            }
        }
    });
    // Load once while the game boots. This thread remains the sole owner of native engine state.
    let mut session=Session::new(&assets,floor(),vec![],[0.,0.3,0.],0.)?;
    session.set_respawn_in_place(true);
    let p=session.activate([0.,0.3,0.],0.)?;
    let mut state=BridgeState {rest:Some(avatar::bind_pose(&assets,&p.names)?),session:Some(session),..Default::default()};
    eprintln!("Skate session warm and ready");
    for (request,reply) in received {
        let response=match apply(request,&mut state,&assets) {
            Ok(response) => response,
            Err(error) => {
                state.active=false;
                if let Some(job)=state.collision.take() {let _=job.join();}
                serde_json::json!({"ok":false,"error":error})
            }
        };
        let _=reply.send(response);
    }
    Ok(())
}

fn main() {
    // Preserve the upstream producer's packet diagnostics when a numerical guard rejects a tick.
    bevy::prelude::App::new().add_plugins(bevy::log::LogPlugin {filter:"error".into(),..Default::default()});
    // The upstream simulation and animation decoder need more stack than Rust's default.
    let result=std::thread::Builder::new().name("skate".into()).stack_size(32*1024*1024)
        .spawn(|| run().map_err(|e|e.to_string())).expect("simulation thread").join();
    match result {
        Ok(Ok(())) => (),
        Ok(Err(error)) => { eprintln!("{error}"); std::process::exit(1); }
        Err(_) => { eprintln!("Simulation thread panicked"); std::process::exit(1); }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn camera_aspect_is_bounded_and_old_requests_keep_widescreen_default() {
        for invalid in [f32::NAN, f32::INFINITY, 0.0, -1.0, 9.0] {
            assert!(validate_aspect(invalid).is_err());
        }
        let old=r#"{"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1}"#;
        match serde_json::from_str::<Request>(old).unwrap() {
            Request::Step {aspect,..} => assert_eq!(aspect,default_aspect()),
            _ => panic!("Wrong request"),
        }
    }
    #[test]
    fn collision_requires_finite_non_degenerate_surfaces() {
        assert!(validate_geometry(&floor(),&[]).is_ok());
        assert!(validate_geometry(&[],&[]).is_err());
        assert!(validate_geometry(&[[[0.;3];3]],&[]).is_err());
        let mut invalid=floor(); invalid[0][0][0]=f32::NAN;
        assert!(validate_geometry(&invalid,&[]).is_err());
        assert!(validate_geometry(&floor(),&[vec![[0.;3]]]).is_err());
    }
    #[test]
    fn rails_preserve_ledge_height_and_units() {
        let platform = vec![[[0.,1.,0.],[0.,1.,2.],[2.,1.,2.]],[[0.,1.,0.],[2.,1.,2.],[2.,1.,0.]]];
        let found = grind_rails(&platform);
        assert_eq!(found.len(),4);
        assert!(found.iter().flatten().all(|p|(p[1]-1.).abs()<1e-4));
    }
    #[test]
    fn requests_fail_closed_before_activation() {
        let mut state=BridgeState::default();
        let request=serde_json::from_str::<Request>(
            r#"{"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1}"#
        ).unwrap();
        assert!(apply(request,&mut state,Path::new("missing")).is_err());
        assert!(serde_json::from_str::<Request>(r#"{"op":"erase"}"#).is_err());
    }
}
