using System.Numerics;

namespace SkateSchedule;

// Geometry is independent of Unity so winding and dimensions can be checked without the game.
static class RoundCollision
{
    public static IEnumerable<(Vector3 A,Vector3 B,Vector3 C)> Capsule(float radius,float halfCylinder)
    {
        const int sides=16,hemisphereSteps=6;
        if (!float.IsFinite(radius) || !float.IsFinite(halfCylinder) || radius<=0 || halfCylinder<0)
            throw new ArgumentOutOfRangeException(nameof(radius));
        var rings=new List<Vector3[]>();
        for (int half=0;half<2;half++) for (int row=0;row<=hemisphereSteps;row++)
        {
            float angle=(half*hemisphereSteps+row)*MathF.PI/(2*hemisphereSteps);
            var ring=new Vector3[sides];
            for (int side=0;side<sides;side++)
            {
                float around=side*MathF.Tau/sides;
                ring[side]=new Vector3(radius*MathF.Sin(angle)*MathF.Cos(around),
                    (half==0?halfCylinder:-halfCylinder)+radius*MathF.Cos(angle),
                    radius*MathF.Sin(angle)*MathF.Sin(around));
            }
            rings.Add(ring);
        }
        (Vector3,Vector3,Vector3) Outward(Vector3 a,Vector3 b,Vector3 c)
        {
            Vector3 centre=(a+b+c)/3;
            var axisPoint=new Vector3(0,Math.Clamp(centre.Y,-halfCylinder,halfCylinder),0);
            if (Vector3.Dot(Vector3.Cross(b-a,c-a),centre-axisPoint)<0) (b,c)=(c,b);
            return (a,b,c);
        }
        for (int row=0;row<rings.Count-1;row++) for (int side=0;side<sides;side++)
        {
            int next=(side+1)%sides;
            var a=rings[row][side]; var b=rings[row][next];
            var c=rings[row+1][next]; var d=rings[row+1][side];
            if (Vector3.Cross(b-a,c-a).LengthSquared()>1e-12f) yield return Outward(a,b,c);
            if (Vector3.Cross(c-a,d-a).LengthSquared()>1e-12f) yield return Outward(a,c,d);
        }
    }
}
