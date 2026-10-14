use skate_core::{math::Vector3,physics::{
    board_step::CollisionBody,
    board_world::{BoardWorld,BoardWorldVolume,ContactRetentionSettings,WorldTriangle},
    collision::{Sphere,TriangleFeature,WorldContactSettings},
    contact::RetailContactMaterial,
    world_contact::ContactPrimitive,
}};

fn side_contact(floor_on_both_sides:bool,top:f32,seam_filter:bool,tilt:f32) -> bool {
    let material=RetailContactMaterial {static_friction:0.8,dynamic_friction:0.6,restitution:0.};
    let mut faces=Vec::new();
    let rotate=|p:Vector3| Vector3::new(p.x*tilt.cos()-p.y*tilt.sin(),p.x*tilt.sin()+p.y*tilt.cos(),p.z);
    let mut face=|points:[[f32;3];3],tag| faces.push(WorldTriangle::from_vertices(
        points.map(|p|rotate(Vector3::new(p[0],p[1],p[2]))),material,tag,
        TriangleFeature::ONE_SIDED | 0xe0,[1.;3],0.).unwrap());
    let left=if floor_on_both_sides {-2.} else {0.};
    let floor=if floor_on_both_sides {0.} else {top};
    face([[left,floor,-2.],[left,floor,2.],[2.,floor,2.]],1);
    face([[left,floor,-2.],[2.,floor,2.],[2.,floor,-2.]],1);
    // Buried pavement side, with its outward normal facing the wheel on the negative-X side.
    face([[0.,top,-2.],[0.,-1.,-2.],[0.,-1.,2.]],99);
    face([[0.,top,-2.],[0.,-1.,2.],[0.,top,2.]],99);
    let mut world=BoardWorld::new(faces);
    if seam_filter {world.enable_imported_floor_seams();}
    let volumes=[BoardWorldVolume {
        body:CollisionBody::Attached(0),
        primitive:ContactPrimitive::Sphere(Sphere {center:rotate(Vector3::new(-0.02,floor-0.03,0.)),radius:0.05}),
        linear_velocity:rotate(Vector3::new(5.,0.,0.)),material,
    }];
    world.query_primitives(&volumes,WorldContactSettings {
        volume_padding:0.,maximum_separating_distance:0.,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false,
    },ContactRetentionSettings {capacity:100,duplicate_distance_squared:1e-6,deferred_reduction:false})
        .iter().any(|c|c.contact.tag==99)
}

#[test]
fn buried_pavement_sides_do_not_stop_wheels_below_the_floor_plane() {
    for tilt in [0.,15f32.to_radians(),-15f32.to_radians()] {
        assert!(side_contact(true,0.,false,tilt),"Regression fixture must reproduce an unfiltered side contact");
        assert!(!side_contact(true,0.,true,tilt),"A fully buried side still snags a wheel below the floor plane: {tilt}");
    }
}

#[test]
fn exposed_curbs_and_floor_edges_keep_their_side_contacts() {
    for tilt in [0.,15f32.to_radians(),-15f32.to_radians()] {
        assert!(side_contact(false,0.09,true,tilt),"A real raised curb lost its blocking side");
        assert!(side_contact(false,0.,true,tilt),"An exposed floor edge lost its blocking side");
        assert!(side_contact(true,0.5,true,tilt),"A wall rising above continuous ground lost its blocking side");
    }
}

fn floor_edge_contact(slope:Option<f32>,height:f32,gap:f32,seam_filter:bool) -> bool {
    let material=RetailContactMaterial {static_friction:0.8,dynamic_friction:0.6,restitution:0.};
    let mut faces=Vec::new();
    let mut face=|points:[[f32;3];3],tag| faces.push(WorldTriangle::from_vertices(
        points.map(|p|Vector3::new(p[0],p[1],p[2])),material,tag,
        TriangleFeature::ONE_SIDED | 0xe0,[1.;3],0.).unwrap());
    face([[0.,height,-2.],[0.,height,2.],[2.,height,2.]],99);
    face([[0.,height,-2.],[2.,height,2.],[2.,height,-2.]],99);
    if let Some(slope)=slope {
        face([[-2.,slope*2.,-2.],[-2.,slope*2.,2.],[-gap,slope*gap,2.]],1);
        face([[-2.,slope*2.,-2.],[-gap,slope*gap,2.],[-gap,slope*gap,-2.]],1);
    }
    let mut world=BoardWorld::new(faces);
    if seam_filter {world.enable_imported_floor_seams();}
    let volumes=[BoardWorldVolume {body:CollisionBody::Attached(0),
        primitive:ContactPrimitive::Sphere(Sphere {center:Vector3::new(-0.02,height+0.02,0.),radius:0.05}),
        linear_velocity:Vector3::new(5.,0.,0.),material}];
    world.query_primitives(&volumes,WorldContactSettings {
        volume_padding:0.,maximum_separating_distance:0.,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false,
    },ContactRetentionSettings {capacity:100,duplicate_distance_squared:1e-6,deferred_reduction:false})
        .iter().any(|c|c.contact.tag==99 && c.contact.normal.x < -0.05)
}

#[test]
fn a_close_floor_plane_does_not_create_contacts_with_distant_edges() {
    use skate_core::physics::{collision::{sphere_triangle_world_contact,intersect_sphere_triangle},world_contact::{primitive_triangle_world_contacts,triangle_from_volume}};
    let triangle=triangle_from_volume([
        Vector3::new(0.,0.,-2.),Vector3::new(2.,0.,2.),Vector3::new(2.,0.,-2.)
    ],0.,[1.;3],TriangleFeature::ONE_SIDED|0xe0);
    let query=WorldContactSettings {volume_padding:0.,maximum_separating_distance:0.,edge_cos_bend_normal_threshold:-1.,convexity_epsilon:0.,is_object:false};
    for (center,contact) in [(Vector3::new(-0.02,0.02,0.),false),(Vector3::new(1.01,0.02,0.),true)] {
        let sphere=Sphere {center,radius:0.05};
        assert_eq!(primitive_triangle_world_contacts(ContactPrimitive::Sphere(sphere),triangle,Vector3::ZERO,query).is_some(),contact);
        assert_eq!(sphere_triangle_world_contact(sphere,triangle,Vector3::ZERO,query).is_some(),contact);
        for triangle_is_a in [false,true] {assert_eq!(intersect_sphere_triangle(sphere,triangle,0.,triangle_is_a).is_some(),contact);}
    }
}

#[test]
fn shallow_hill_joins_do_not_produce_blocking_floor_edge_contacts() {
    for slope in [-0.268,-0.087,0.087,0.268] {
        assert!(floor_edge_contact(Some(slope),0.001,0.,false),"Hill fixture did not generate a floor-edge contact");
        assert!(!floor_edge_contact(Some(slope),0.001,0.,true),"A continuous shallow hill still has a blocking edge: {slope}");
        assert!(!floor_edge_contact(Some(slope),0.003,0.024,true),"A millimetre road join with a small authored gap still snags: {slope}");
    }
    assert!(floor_edge_contact(Some(0.),0.09,0.,true),"A raised platform lost its real exposed edge");
    assert!(floor_edge_contact(Some(0.),0.01,0.,true),"A one-centimetre step lost its exposed edge");
    assert!(floor_edge_contact(Some(0.),0.,0.04,true),"A larger gap lost its exposed edge");
    assert!(floor_edge_contact(None,0.,0.,true),"A floor with no continuation lost its exposed edge");
}
