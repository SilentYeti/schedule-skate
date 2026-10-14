using UnityEngine;
using NVector=System.Numerics.Vector3;
using NMatrix=System.Numerics.Matrix4x4;
using NQuaternion=System.Numerics.Quaternion;

namespace SkateSchedule;

static class CollisionExport
{
    public const float Radius=CollisionCrop.Radius;
    private static readonly Dictionary<(IntPtr,bool),CollisionGeometry> meshes=new();
    private static readonly Dictionary<(float,float),CollisionGeometry> rounded=new();
    public static void Clear() {meshes.Clear(); rounded.Clear();}
    internal static NVector Numeric(Vector3 v) => new(v.x,v.y,v.z);
    private static NMatrix Numeric(Matrix4x4 m) => new(
        m.m00,m.m10,m.m20,m.m30,m.m01,m.m11,m.m21,m.m31,
        m.m02,m.m12,m.m22,m.m32,m.m03,m.m13,m.m23,m.m33);
    private static CollisionGeometry Round(float radius,float half)
    {
        if (rounded.TryGetValue((radius,half),out var cached)) return cached;
        var vertices=RoundCollision.Capsule(radius,half).SelectMany(f=>new[]{f.A,f.B,f.C}).ToArray();
        var result=new CollisionGeometry {Vertices=vertices,Indices=Enumerable.Range(0,vertices.Length).ToArray()};
        rounded.Add((radius,half),result); return result;
    }
    // Only snapshot Unity metadata here. No triangle transforms or clipping on the game thread.
    public static IEnumerable<CollisionInstance> Snapshot(Vector3 centre,int mask,Transform rider,Transform board)
    {
        var colliders=Physics.OverlapBox(centre,new Vector3(Radius,25,Radius),Quaternion.identity,mask,QueryTriggerInteraction.Ignore);
        RuntimeDiagnostics.Record(new {kind="snapshot_overlap",count=colliders.Length,mask,
            centre=new[]{centre.x,centre.y,centre.z}});
        foreach (var collider in colliders)
        {
            var transform=collider.transform;
            if (!collider.enabled || transform.IsChildOf(rider) || transform.IsChildOf(board) ||
                collider.attachedRigidbody!=null && !collider.attachedRigidbody.isKinematic) continue;
            CollisionGeometry geometry;
            var matrix=Numeric(transform.localToWorldMatrix);
            if (collider.TryCast<BoxCollider>() is { } box)
            {
                var corners=new NVector[8];
                var midpoint=Numeric(box.center); var size=Numeric(box.size)*0.5f;
                for (int i=0;i<8;i++) corners[i]=midpoint+size*new NVector((i&1)==0?-1:1,(i&2)==0?-1:1,(i&4)==0?-1:1);
                geometry=new CollisionGeometry {Vertices=corners,Indices=new[]{
                    0,4,6,0,6,2,1,3,7,1,7,5,0,1,5,0,5,4,
                    2,6,7,2,7,3,0,2,3,0,3,1,4,5,7,4,7,6}};
            }
            else if (collider.TryCast<SphereCollider>() is { } sphere)
            {
                var scale=transform.lossyScale;
                float radius=sphere.radius*Math.Max(Math.Abs(scale.x),Math.Max(Math.Abs(scale.y),Math.Abs(scale.z)));
                geometry=Round(radius,0);
                matrix=NMatrix.CreateTranslation(Numeric(transform.TransformPoint(sphere.center)));
            }
            else if (collider.TryCast<CapsuleCollider>() is { } capsule)
            {
                var scale=transform.lossyScale;
                float[] sizes={Math.Abs(scale.x),Math.Abs(scale.y),Math.Abs(scale.z)};
                int axis=capsule.direction;
                float radius=capsule.radius*Math.Max(sizes[(axis+1)%3],sizes[(axis+2)%3]);
                float half=Math.Max(0,capsule.height*sizes[axis]*0.5f-radius);
                Vector3 direction=axis==0?Vector3.right:axis==1?Vector3.up:Vector3.forward;
                var rotation=Quaternion.FromToRotation(Vector3.up,transform.TransformDirection(direction));
                geometry=Round(radius,half);
                matrix=NMatrix.CreateFromQuaternion(new NQuaternion(rotation.x,rotation.y,rotation.z,rotation.w))*
                    NMatrix.CreateTranslation(Numeric(transform.TransformPoint(capsule.center)));
            }
            else if (collider.TryCast<MeshCollider>() is { } meshCollider)
            {
                var mesh=meshCollider.sharedMesh;
                if (mesh==null) continue;
                var key=(mesh.Pointer,meshCollider.convex);
                if (!meshes.TryGetValue(key,out geometry!))
                {
                    if (mesh.isReadable && !meshCollider.convex)
                        geometry=new CollisionGeometry {Vertices=mesh.vertices.ToArray().Select(Numeric).ToArray(),
                            Indices=mesh.triangles.ToArray()};
                    else geometry=CollisionCache.Get(mesh,meshCollider.convex);
                    meshes.Add(key,geometry);
                }
            }
            else if (collider.TryCast<TerrainCollider>() is { } terrain)
            {
                var data=terrain.terrainData;
                int n=data.heightmapResolution-1;
                var size=data.size;
                Vector3 local=transform.InverseTransformPoint(centre);
                int x0=Math.Clamp((int)Math.Floor((local.x-Radius)/size.x*n),0,n);
                int x1=Math.Clamp((int)Math.Ceiling((local.x+Radius)/size.x*n),0,n);
                int z0=Math.Clamp((int)Math.Floor((local.z-Radius)/size.z*n),0,n);
                int z1=Math.Clamp((int)Math.Ceiling((local.z+Radius)/size.z*n),0,n);
                int width=x1-x0+1;
                int height=z1-z0+1;
                var nativeHeights=data.GetHeights(x0,z0,width,height);
                var flat=new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<float>(width*height);
                // Bulk-copy the rectangular array; GetHeight per vertex causes thousands of native calls.
                Il2CppSystem.Buffer.BlockCopy(nativeHeights.Cast<Il2CppSystem.Array>(),0,flat.Cast<Il2CppSystem.Array>(),0,width*height*4);
                var heights=flat.ToArray();
                geometry=new CollisionGeometry {Terrain=new TerrainSamples(heights,width,height,x0,z0,size.x/n,size.z/n,size.y)};
            }
            else throw new InvalidOperationException($"Unsupported static collider: {collider.name} ({collider.GetIl2CppType().Name}).");
            yield return new CollisionInstance(geometry,matrix);
        }

    }
}
