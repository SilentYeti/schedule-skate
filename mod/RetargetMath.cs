using System.Numerics;

namespace SkateSchedule;

static class RetargetMath
{
    public static readonly (string Target,string Source)[] Mapping={
        ("Hips","HIPS"),("Spine","SPINE"),("Spine1","SPINE1"),("Spine2","SPINE3"),
        ("Neck","NECK1"),("Head","HEAD"),
        ("LeftShoulder","LEFTSHOULDER"),("LeftArm","LEFTARM"),("LeftForeArm","LEFTFOREARM"),("LeftHand","LEFTHAND"),
        ("RightShoulder","RIGHTSHOULDER"),("RightArm","RIGHTARM"),("RightForeArm","RIGHTFOREARM"),("RightHand","RIGHTHAND"),
        ("LeftUpLeg","LEFTUPLEG"),("LeftLeg","LEFTLEG"),("LeftFoot","LEFTFOOT"),("LeftToeBase","LEFTTOEBASE"),
        ("RightUpLeg","RIGHTUPLEG"),("RightLeg","RIGHTLEG"),("RightFoot","RIGHTFOOT"),("RightToeBase","RIGHTTOEBASE")};
    public static Quaternion Rotation(Quaternion source,Quaternion sourceBind,Quaternion targetBind) =>
        Quaternion.Normalize(source*Quaternion.Inverse(sourceBind)*targetBind);
    public static Quaternion AlignBind(Quaternion targetBind,Vector3 targetDirection,Vector3 sourceDirection)
    {
        float targetLength=targetDirection.LengthSquared(),sourceLength=sourceDirection.LengthSquared();
        if (!float.IsFinite(targetLength) || !float.IsFinite(sourceLength) || targetLength<1e-10f || sourceLength<1e-10f)
            throw new IOException("Avatar has an invalid arm segment.");
        var from=Vector3.Normalize(targetDirection); var to=Vector3.Normalize(sourceDirection);
        float dot=Math.Clamp(Vector3.Dot(from,to),-1,1);
        var swing=dot<-0.99999f
            ? Quaternion.CreateFromAxisAngle(Vector3.Normalize(Vector3.Cross(from,Math.Abs(from.X)<0.9f?Vector3.UnitX:Vector3.UnitY)),MathF.PI)
            : Quaternion.Normalize(new Quaternion(Vector3.Cross(from,to),1+dot));
        return Quaternion.Normalize(swing*targetBind);
    }
    public static string? ArmChild(string target) => target.EndsWith("Shoulder")?target.Replace("Shoulder","Arm"):
        target.EndsWith("ForeArm")?target.Replace("ForeArm","Hand"):
        target.EndsWith("Arm")?target.Replace("Arm","ForeArm"):null;
    public static Vector3 HipPosition(Vector3 board,Vector3 sourceHip,Vector3 sourceBoard,float scale) =>
        board+(sourceHip-sourceBoard)*scale;
    public static Vector3 FootCorrection(Vector3 actualLeft,Vector3 actualRight,Vector3 desiredLeft,Vector3 desiredRight) =>
        (desiredLeft+desiredRight-actualLeft-actualRight)*0.5f;
    public static float LegScale(float target,float source)
    {
        float scale=target/source;
        if (!float.IsFinite(scale) || source<=0 || target<=0 || scale<0.25f || scale>4)
            throw new IOException($"Avatar has invalid leg proportions (target={target:F4}, source={source:F4}).");
        return scale;
    }
}
