// Only metadata access is needed to exercise the real cache without loading Unity.
namespace UnityEngine;

struct Vector3(float x,float y,float z)
{
    public float x=x,y=y,z=z;
}
struct Bounds(Vector3 center,Vector3 extents)
{
    public Vector3 center=center,extents=extents;
}
sealed class Mesh
{
    public string name="fixture";
    public int vertexCount=3;
    public int subMeshCount=1;
    public Bounds bounds=new(new Vector3(0,0,0),new Vector3(1,1,1));
    public uint GetIndexCount(int index) => 3;
}
