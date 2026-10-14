using System.Numerics;

namespace SkateSchedule;

static class CollisionClip
{
    public static bool Outside(Vector3 a,Vector3 b,Vector3 c,Vector3 min,Vector3 max)
    {
        var low=Vector3.Min(a,Vector3.Min(b,c));
        var high=Vector3.Max(a,Vector3.Max(b,c));
        return high.X<min.X || high.Y<min.Y || high.Z<min.Z || low.X>max.X || low.Y>max.Y || low.Z>max.Z;
    }
    public static bool Inside(Vector3 point,Vector3 min,Vector3 max) =>
        point.X>=min.X && point.X<=max.X && point.Y>=min.Y && point.Y<=max.Y && point.Z>=min.Z && point.Z<=max.Z;

    public static List<Vector3> Polygon(Vector3 a,Vector3 b,Vector3 c,Vector3 min,Vector3 max)
    {
        var points=new List<Vector3> {a,b,c};
        for (int axis=0;axis<3;axis++) for (int side=0;side<2;side++)
        {
            if (points.Count==0) return points;
            float Coordinate(Vector3 v) => axis==0?v.X:axis==1?v.Y:v.Z;
            float limit=Coordinate(side==0?min:max);
            float Distance(Vector3 v) => side==0?Coordinate(v)-limit:limit-Coordinate(v);
            var output=new List<Vector3>(points.Count+1);
            var previous=points[^1]; float before=Distance(previous);
            foreach (var current in points)
            {
                float after=Distance(current);
                if ((after>=0)!=(before>=0))
                    output.Add(Vector3.Lerp(previous,current,before/(before-after)));
                if (after>=0) output.Add(current);
                previous=current; before=after;
            }
            points=output;
        }
        return points;
    }
}
