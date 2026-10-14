using System.Diagnostics;
using System.Globalization;
using System.Numerics;
using System.Text.Json;
using SkateSchedule;

if (args.Length==3 && args[0]=="avatar")
{
    using var report=JsonDocument.Parse(File.ReadAllText(args[1]));
    using var fixture=JsonDocument.Parse(File.ReadAllText(args[2]));
    Matrix4x4 Engine(JsonElement value)
    {
        var v=value.EnumerateArray().Select(e=>e.GetSingle()).ToArray();
        for (int i=0;i<16;i++) v[i]*=(i%4==0?-1:1)*(i/4==0?-1:1);
        v[15]=1;
        return new Matrix4x4(v[0],v[1],v[2],v[3],v[4],v[5],v[6],v[7],v[8],v[9],v[10],v[11],v[12],v[13],v[14],v[15]);
    }
    Quaternion Rotation(Matrix4x4 m)
    {
        if (!Matrix4x4.Decompose(m,out _,out var q,out _)) throw new Exception("Avatar matrix is degenerate.");
        return q;
    }
    var entries=fixture.RootElement.GetProperty("bones").EnumerateArray().ToDictionary(b=>b.GetProperty("name").GetString()!);
    var targetBind=new Dictionary<string,Matrix4x4>();
    Matrix4x4 TargetBind(string name)
    {
        if (targetBind.TryGetValue(name,out var cached)) return cached;
        var bone=entries[name];
        var p=bone.GetProperty("position").EnumerateArray().Select(e=>e.GetSingle()).ToArray();
        var q=bone.GetProperty("rotation").EnumerateArray().Select(e=>e.GetSingle()).ToArray();
        string? parent=bone.GetProperty("parent").GetString();
        var local=Matrix4x4.CreateFromQuaternion(new Quaternion(q[0],q[1],q[2],q[3]))*Matrix4x4.CreateTranslation(p[0],p[1],p[2]);
        return targetBind[name]=local*(parent!=null && entries.ContainsKey(parent)?TargetBind(parent):Matrix4x4.Identity);
    }
    var rest=report.RootElement.GetProperty("source_rest").EnumerateArray().Select(Engine).ToArray();
    foreach (var frame in report.RootElement.GetProperty("camera_frames").EnumerateArray())
    {
        var names=frame.GetProperty("names").EnumerateArray().Select(e=>e.GetString()).ToArray();
        int Index(string name) => Array.IndexOf(names,name);
        var pose=frame.GetProperty("bones").EnumerateArray().Select(Engine).ToArray();
        var root=Engine(frame.GetProperty("root"));
        float SourceLeg() => Vector3.Distance(rest[Index("LEFTUPLEG")].Translation,rest[Index("LEFTLEG")].Translation)+
            Vector3.Distance(rest[Index("LEFTLEG")].Translation,rest[Index("LEFTFOOT")].Translation);
        float TargetLeg() => Vector3.Distance(TargetBind("mixamorig:LeftUpLeg").Translation,TargetBind("mixamorig:LeftLeg").Translation)+
            Vector3.Distance(TargetBind("mixamorig:LeftLeg").Translation,TargetBind("mixamorig:LeftFoot").Translation);
        float scale=RetargetMath.LegScale(TargetLeg(),SourceLeg());
        var rotations=new Dictionary<string,Quaternion>();
        foreach (var (target,source) in RetargetMath.Mapping)
        {
            int index=Index(source);
            if (index<0) throw new Exception($"Missing source bone {source}");
            var tq=Rotation(TargetBind("mixamorig:"+target)); var sq=Rotation(rest[index]);
            string calibration=target.EndsWith("Hand")?target.Replace("Hand","ForeArm"):target;
            string? child=RetargetMath.ArmChild(calibration);
            if (child!=null)
            {
                string parentSource=RetargetMath.Mapping.Single(m=>m.Target==calibration).Source;
                string childSource=RetargetMath.Mapping.Single(m=>m.Target==child).Source;
                tq=RetargetMath.AlignBind(tq,TargetBind("mixamorig:"+child).Translation-TargetBind("mixamorig:"+calibration).Translation,
                    rest[Index(childSource)].Translation-rest[Index(parentSource)].Translation);
            }
            var neutral=RetargetMath.Rotation(sq,sq,tq);
            if (Math.Abs(Quaternion.Dot(neutral,tq))<0.99999f) throw new Exception("Avatar bind calibration changed its neutral pose.");
            rotations["mixamorig:"+target]=RetargetMath.Rotation(Rotation(pose[index]*root),sq,tq);
        }
        var sourceBoard=(pose[Index("SKATEBOARD_ROOT")]*root).Translation;
        var hips=RetargetMath.HipPosition(sourceBoard,(pose[Index("HIPS")]*root).Translation,sourceBoard,scale);
        var positions=new Dictionary<string,Vector3> {{"mixamorig:Hips",hips}};
        Vector3 TargetPosition(string name)
        {
            if (positions.TryGetValue(name,out var cached)) return cached;
            var entry=entries[name]; string parent=entry.GetProperty("parent").GetString()!;
            var p=entry.GetProperty("position").EnumerateArray().Select(e=>e.GetSingle()).ToArray();
            return positions[name]=TargetPosition(parent)+Vector3.Transform(new Vector3(p[0],p[1],p[2]),rotations[parent]);
        }
        var actualFeet=new List<Vector3>(); var desiredFeet=new List<Vector3>();
        foreach (string side in new[]{"Left","Right"})
        {
            var expected=sourceBoard+((pose[Index(side.ToUpperInvariant()+"FOOT")]*root).Translation-sourceBoard)*scale;
            actualFeet.Add(TargetPosition("mixamorig:"+side+"Foot")); desiredFeet.Add(expected);
        }
        var correction=RetargetMath.FootCorrection(actualFeet[0],actualFeet[1],desiredFeet[0],desiredFeet[1]);
        var errors=actualFeet.Select((v,i)=>Vector3.Distance(v+correction,desiredFeet[i])).ToArray();
        if (errors.Any(error=>!float.IsFinite(error) || error>0.1f)) throw new Exception("Retargeted feet drifted over 10 cm from the source pose.");
        float armDot=1;
        foreach (var (target,source) in RetargetMath.Mapping)
        {
            string? child=RetargetMath.ArmChild(target);
            if (child==null) continue;
            string childSource=RetargetMath.Mapping.Single(m=>m.Target==child).Source;
            var actual=Vector3.Normalize(TargetPosition("mixamorig:"+child)-TargetPosition("mixamorig:"+target));
            var desired=Vector3.Normalize((pose[Index(childSource)]*root).Translation-(pose[Index(source)]*root).Translation);
            armDot=Math.Min(armDot,Vector3.Dot(actual,desired));
        }
        if (!float.IsFinite(armDot) || armDot<0.999f) throw new Exception($"Retargeted arm direction diverged: {armDot}");
        Console.WriteLine($"Avatar fixture: {rotations.Count} calibrated bones, leg scale={scale:F3}, foot errors={string.Join(", ",errors.Select(v=>v.ToString("F3")))} m, arm direction dot={armDot:F5}.");
    }
    return;
}
if (args.Length==2 && args[0]=="camera")
{
    using var document=JsonDocument.Parse(File.ReadAllText(args[1]));
    foreach (var frame in document.RootElement.GetProperty("camera_frames").EnumerateArray())
    {
        var camera=frame.GetProperty("camera");
        var basis=camera.GetProperty("basis").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
        CameraMath.Validate(basis);
        var (forward,up)=CameraMath.Directions(basis);
        var position=camera.GetProperty("position").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
        var root=frame.GetProperty("root").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
        var delta=new Vector3(-root[12],root[13]+1,root[14])-new Vector3(-position[0],position[1],position[2]);
        float dot=Vector3.Dot(Vector3.Normalize(forward),Vector3.Normalize(delta));
        if (dot<0.9f || up.Y<0.5f) throw new Exception($"Camera faces away from skater: {dot}");
        var origin=new Vector3(100,5,-50); var modelOffset=new Vector3(1.8f,0,0.7f);
        var placed=CameraMath.Position(position,origin,modelOffset);
        var displayedTarget=origin+new Vector3(-root[12],root[13]+1,root[14])+modelOffset;
        if (Math.Abs(Vector3.Dot(Vector3.Normalize(forward),Vector3.Normalize(displayedTarget-placed))-dot)>1e-5)
            throw new Exception("Camera did not follow the board's model offset.");
        Console.WriteLine($"Shared camera transform: toward skater dot={dot:F5}, up.Y={up.Y:F5}");
    }
    return;
}
if (args.Length!=6 || args[0]!="crop") throw new ArgumentException("avatar <report> <rig.json> | camera <report> | crop <scene.json> <x> <y> <z> <output.json>");
string scene=Path.GetFullPath(args[1]),directory=Path.GetDirectoryName(scene)!;
using var sceneDocument=JsonDocument.Parse(File.ReadAllText(scene));
var loaded=new Dictionary<string,CollisionGeometry>();
var instances=new List<CollisionInstance>();
foreach (var entry in sceneDocument.RootElement.GetProperty("instances").EnumerateArray())
{
    string file=entry.GetProperty("file").GetString()!;
    if (Path.GetFileName(file)!=file) throw new IOException("Unsafe fixture path");
    if (!loaded.TryGetValue(file,out var geometry)) loaded.Add(file,geometry=CollisionGeometry.Read(Path.Combine(directory,file)));
    var m=entry.GetProperty("matrix").EnumerateArray().Select(v=>v.GetSingle()).ToArray();
    if (m.Length!=16 || m.Any(v=>!float.IsFinite(v))) throw new IOException("Invalid fixture transform");
    instances.Add(new CollisionInstance(geometry,new Matrix4x4(m[0],m[1],m[2],m[3],m[4],m[5],m[6],m[7],m[8],m[9],m[10],m[11],m[12],m[13],m[14],m[15])));
}
var centre=new Vector3(float.Parse(args[2],CultureInfo.InvariantCulture),float.Parse(args[3],CultureInfo.InvariantCulture),float.Parse(args[4],CultureInfo.InvariantCulture));
var elapsed=new List<double>();
float[][][]? triangles=null;
for (int i=0;i<5;i++)
{
    var watch=Stopwatch.StartNew();
    triangles=CollisionCrop.Gather(instances,centre,centre);
    elapsed.Add(watch.Elapsed.TotalMilliseconds);
}
File.WriteAllText(args[5],JsonSerializer.Serialize(new {op="activate",triangles,rails=Array.Empty<float[][]>(),spawn=new[]{0f,0f,0f},heading=0f}));
Console.WriteLine(JsonSerializer.Serialize(new {instances=instances.Count,triangles=triangles!.Length,crop_ms=elapsed,output=args[5]}));
