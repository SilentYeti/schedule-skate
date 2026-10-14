using Il2CppScheduleOne.AvatarFramework.Animation;
using GameAvatar=Il2CppScheduleOne.AvatarFramework.Avatar;
using UnityEngine;
using NQuaternion=System.Numerics.Quaternion;

namespace SkateSchedule;

sealed class AvatarRetargeter : IDisposable
{
    // Global bind calibration also collapses the source's extra spine/neck joints onto the target rig.
    private static readonly (string Target,string Source)[] mapping=RetargetMath.Mapping;
    private sealed record Bone(Transform Transform,int Source,Quaternion SourceBind,Quaternion TargetBind,
        Vector3 SavedPosition,Quaternion SavedRotation);
    private readonly Bone[] bones;
    private readonly AvatarAnimation animation;
    private readonly Animator animator;
    private readonly AvatarIKController? ik;
    private readonly Il2CppRootMotion.FinalIK.BipedIK? bodyIk;
    private readonly AvatarLookController? look;
    private readonly bool animationEnabled,animatorEnabled,ikEnabled,bodyIkEnabled,lookEnabled;
    private readonly float legScale;
    private readonly Bone leftFoot,rightFoot;
    private readonly Bone[] hands;
    private bool held;

    private static NQuaternion Numeric(Quaternion q) => new(q.x,q.y,q.z,q.w);
    private static Quaternion Unity(NQuaternion q) => new(q.X,q.Y,q.Z,q.W);

    public AvatarRetargeter(GameAvatar avatar,Reply initial)
    {
        if (initial.rest.Length!=initial.names.Length || initial.rest.Length==0)
            throw new IOException("The native bridge needs an avatar reference pose. Restart the updated bridge.");
        animation=avatar.Animation;
        animator=animation._animator;
        ik=animation._ikController; bodyIk=ik!=null?ik.BodyIK:null; look=avatar.LookController;
        animationEnabled=animation.enabled; animatorEnabled=animator.enabled;
        ikEnabled=ik!=null && ik.enabled; bodyIkEnabled=bodyIk!=null && bodyIk.enabled; lookEnabled=look!=null && look.enabled;
        var renderers=avatar.GetComponentsInChildren<SkinnedMeshRenderer>(false).ToArray();
        foreach (var renderer in renderers.OrderByDescending(r=>r.name.StartsWith("Body",StringComparison.OrdinalIgnoreCase)))
        {
            if (renderer.sharedMesh==null) continue;
            var transforms=renderer.bones.ToArray();
            if (!mapping.All(m=>transforms.Any(t=>t!=null && t.name=="mixamorig:"+m.Target))) continue;
            var hips=transforms.First(t=>t!=null && t.name=="mixamorig:Hips");
            if (avatar.HipBone!=null && hips.Pointer!=avatar.HipBone.Pointer) continue;
            var bind=renderer.sharedMesh.bindposes.ToArray();
            if (bind.Length!=transforms.Length) continue;
            var selected=new List<Bone>();
            var targetBones=new Dictionary<string,Transform>();
            foreach (var (target,source) in mapping)
            {
                int ti=Array.FindIndex(transforms,t=>t!=null && t.name=="mixamorig:"+target);
                int si=Array.IndexOf(initial.names,source);
                if (si<0) throw new IOException($"The simulation has no {source} bone.");
                var bone=transforms[ti];
                var rest=bind[ti].inverse;
                var targetBind=Quaternion.Inverse(avatar.transform.rotation)*renderer.transform.rotation*rest.rotation;
                // The source reference has bent/lowered arms; rotation deltas alone preserve that rig mismatch.
                string calibration=target.EndsWith("Hand")?target.Replace("Hand","ForeArm"):target;
                string? child=RetargetMath.ArmChild(calibration);
                if (child!=null)
                {
                    int parentIndex=Array.FindIndex(transforms,t=>t!=null && t.name=="mixamorig:"+calibration);
                    var childBone=transforms.First(t=>t!=null && t.name=="mixamorig:"+child);
                    var parentBind=Quaternion.Inverse(avatar.transform.rotation)*renderer.transform.rotation*bind[parentIndex].inverse.rotation;
                    string parentSource=mapping.Single(m=>m.Target==calibration).Source;
                    string childSource=mapping.Single(m=>m.Target==child).Source;
                    Vector3 Reference(string name) => (Vector3)Space.Matrix(initial.rest[Array.IndexOf(initial.names,name)]).GetColumn(3);
                    targetBind=Unity(RetargetMath.AlignBind(Numeric(targetBind),CollisionExport.Numeric(parentBind*childBone.localPosition),
                        CollisionExport.Numeric(Reference(childSource)-Reference(parentSource))));
                }
                selected.Add(new Bone(bone,si,Space.Matrix(initial.rest[si]).rotation,targetBind,bone.localPosition,bone.localRotation));
                targetBones.Add(target,bone);
            }
            float Length(string upper,string lower,string foot) =>
                Vector3.Distance(targetBones[upper].position,targetBones[lower].position)+Vector3.Distance(targetBones[lower].position,targetBones[foot].position);
            Vector3 Source(string name) => (Vector3)Space.Matrix(initial.rest[Array.IndexOf(initial.names,name)]).GetColumn(3);
            float sourceLength=Vector3.Distance(Source("LEFTUPLEG"),Source("LEFTLEG"))+Vector3.Distance(Source("LEFTLEG"),Source("LEFTFOOT"));
            float targetLength=(Length("LeftUpLeg","LeftLeg","LeftFoot")+Length("RightUpLeg","RightLeg","RightFoot"))*0.5f;
            MelonLoader.MelonLogger.Msg($"Avatar rig: {renderer.name}; live leg={targetLength:F4} m; source leg={sourceLength:F4} m.");
            legScale=RetargetMath.LegScale(targetLength,sourceLength);
            bones=selected.ToArray();
            leftFoot=bones.Single(b=>initial.names[b.Source]=="LEFTFOOT");
            rightFoot=bones.Single(b=>initial.names[b.Source]=="RIGHTFOOT");
            hands=new[]{bones.Single(b=>initial.names[b.Source]=="LEFTHAND"),bones.Single(b=>initial.names[b.Source]=="RIGHTHAND")};
            return;
        }
        throw new IOException("Could not find the local avatar's complete Mixamo rig and bind poses.");
    }
    public void Hold()
    {
        held=true;
        animation.enabled=false; animator.enabled=false;
        if (ik!=null) ik.enabled=false;
        if (bodyIk!=null) bodyIk.enabled=false;
        if (look!=null) look.enabled=false;
    }
    public int BoneCount => bones.Length;
    public float Scale => legScale;
    public void Apply(Reply frame,Transform board,Vector3 origin)
    {
        var root=Space.Matrix(frame.root);
        foreach (var bone in bones)
        {
            var source=root*Space.Matrix(frame.bones[bone.Source]);
            bone.Transform.rotation=Unity(RetargetMath.Rotation(Numeric(source.rotation),Numeric(bone.SourceBind),Numeric(bone.TargetBind)));
        }
        var hip=root*Space.Matrix(frame.bones[bones[0].Source]);
        var sourceBoard=frame.BoardPose();
        bool detached=CollisionFocus.Detached(frame.state);
        var position=detached?RetargetMath.HipPosition(CollisionExport.Numeric(origin+(Vector3)root.GetColumn(3)),
            CollisionExport.Numeric((Vector3)hip.GetColumn(3)),CollisionExport.Numeric((Vector3)root.GetColumn(3)),legScale):
            RetargetMath.HipPosition(CollisionExport.Numeric(board.position),
            CollisionExport.Numeric((Vector3)hip.GetColumn(3)),CollisionExport.Numeric((Vector3)sourceBoard.GetColumn(3)),legScale);
        bones[0].Transform.position=new Vector3(position.X,position.Y,position.Z);
        if (detached)
        {
            if (frame.carry_hand is int hand)
            {
                var sourceHand=origin+(Vector3)(root*Space.Matrix(frame.bones[hands[hand].Source])).GetColumn(3);
                var targetHand=hands[hand].Transform.position;
                // Target limb lengths differ; move the held board with its hand without changing native grip/rotation.
                var adjusted=RetargetMath.CarriedBoardPosition(CollisionExport.Numeric(board.position),
                    CollisionExport.Numeric(sourceHand),CollisionExport.Numeric(targetHand));
                board.position=new Vector3(adjusted.X,adjusted.Y,adjusted.Z);
                if (Time.frameCount%4==0)
                    RuntimeDiagnostics.Record(new {kind="carried_board",frame.tick,hand,
                        source_hand=new[]{sourceHand.x,sourceHand.y,sourceHand.z},
                        target_hand=new[]{targetHand.x,targetHand.y,targetHand.z},
                        correction=new[]{targetHand.x-sourceHand.x,targetHand.y-sourceHand.y,targetHand.z-sourceHand.z}});
            }
            return;
        }
        System.Numerics.Vector3 Desired(Bone foot) => RetargetMath.HipPosition(CollisionExport.Numeric(board.position),
            CollisionExport.Numeric((Vector3)(root*Space.Matrix(frame.bones[foot.Source])).GetColumn(3)),
            CollisionExport.Numeric((Vector3)sourceBoard.GetColumn(3)),legScale);
        // Different hip-to-leg offsets otherwise sink both feet even when their limb lengths match.
        var correction=RetargetMath.FootCorrection(CollisionExport.Numeric(leftFoot.Transform.position),CollisionExport.Numeric(rightFoot.Transform.position),
            Desired(leftFoot),Desired(rightFoot));
        bones[0].Transform.position+=new Vector3(correction.X,correction.Y,correction.Z);
    }
    public void Dispose()
    {
        if (!held) return;
        held=false;
        void Restore(Action action)
        {
            try {action();}
            catch (Exception error) {MelonLoader.MelonLogger.Warning($"Could not restore a removed avatar object: {error.Message}");}
        }
        foreach (var bone in bones)
            Restore(()=>{if (bone.Transform!=null) {bone.Transform.localPosition=bone.SavedPosition; bone.Transform.localRotation=bone.SavedRotation;}});
        Restore(()=>{if (animation!=null) animation.enabled=animationEnabled;});
        Restore(()=>{if (animator!=null) animator.enabled=animatorEnabled;});
        Restore(()=>{if (ik!=null) ik.enabled=ikEnabled;});
        Restore(()=>{if (bodyIk!=null) bodyIk.enabled=bodyIkEnabled;});
        Restore(()=>{if (look!=null) look.enabled=lookEnabled;});
    }
}
