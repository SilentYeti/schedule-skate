using System.Numerics;

namespace SkateSchedule;

static class CameraMath
{
    public static Vector3 Position(float[] position,Vector3 origin,Vector3 displayOffset) =>
        origin+new Vector3(-position[0],position[1],position[2])+displayOffset;
    public static float Heading(Vector3 front,Vector3 rear)
    {
        var direction=front-rear;
        if (!float.IsFinite(direction.X) || !float.IsFinite(direction.Z) || direction.X*direction.X+direction.Z*direction.Z<1e-8f)
            throw new IOException("The skateboard has no usable forward axis.");
        return MathF.Atan2(-direction.X,direction.Z);
    }
    public static (Vector3 Forward,Vector3 Up) Directions(float[] basis)
    {
        // The engine publishes spatial columns: right, up, At (+Z), in world space.
        return (new Vector3(-basis[6],basis[7],basis[8]),new Vector3(-basis[3],basis[4],basis[5]));
    }
    public static void Validate(float[] basis)
    {
        if (basis.Length!=9 || basis.Any(v=>!float.IsFinite(v))) throw new IOException("Invalid simulation camera basis.");
        var (forward,up)=Directions(basis);
        if (forward.LengthSquared()<0.5f || up.LengthSquared()<0.5f || Vector3.Cross(forward,up).LengthSquared()<0.25f)
            throw new IOException("Degenerate simulation camera basis.");
    }
}
