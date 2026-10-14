use std::path::Path;
use skate_core::animation::output::{Sqt, sqt_to_matrix, compose_hierarchy_in_place};
use skate_data::{animation_banks::AnimationBanks, animation_frames::AnimationFrames};

/// The stock reference hierarchy supplies anatomical axes, independent of the current riding pose.
pub fn bind_pose(root: &Path, names: &[String]) -> Result<Vec<[f32; 16]>, String> {
    let banks=AnimationBanks::load(root)?;
    let frames=AnimationFrames::from_banks(&banks)?;
    if frames.bone_names!=names { return Err("Avatar reference skeleton differs from simulation".into()); }
    let pose=frames.named_pose("RIG_TPOSE")?;
    let mut matrices=pose.samples.iter().map(|sample| {
        let [sx,sy,sz,x,y,z,w,tx,ty,tz]=sample.map(f32::from_bits);
        sqt_to_matrix(Sqt {scale:[sx,sy,sz,1.0],rotation:[x,y,z,w],translation:[tx,ty,tz,1.0]})
    }).collect::<Vec<_>>();
    compose_hierarchy_in_place(matrices.len() as i32,&frames.parents,0,&mut matrices)
        .map_err(|e|format!("Avatar reference hierarchy: {e:?}"))?;
    let mut result=Vec::with_capacity(matrices.len());
    for matrix in matrices {
        let mut columns=[0.0;16];
        for c in 0..4 {for r in 0..3 {columns[c*4+r]=matrix[c][r];}}
        columns[15]=1.0;
        if columns.iter().any(|v|!v.is_finite()) {return Err("Non-finite avatar reference pose".into());}
        result.push(columns);
    }
    Ok(result)
}
