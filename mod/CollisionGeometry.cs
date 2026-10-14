using System.Numerics;

namespace SkateSchedule;

// These buffers contain no Unity objects and can be processed on a worker thread.
sealed class CollisionGeometry
{
    public Vector3[] Vertices=Array.Empty<Vector3>();
    public int[] Indices=Array.Empty<int>();
    public TerrainSamples? Terrain;
    public void Prepare()
    {
        if (Terrain is not { } data) return;
        Vertices=new Vector3[data.Width*data.Height];
        for (int z=0;z<data.Height;z++) for (int x=0;x<data.Width;x++)
            Vertices[z*data.Width+x]=new Vector3((data.X+x)*data.SpacingX,data.Heights[z*data.Width+x]*data.ScaleY,(data.Z+z)*data.SpacingZ);
        Indices=new int[(data.Width-1)*(data.Height-1)*6];
        int cursor=0;
        for (int z=0;z<data.Height-1;z++) for (int x=0;x<data.Width-1;x++)
        {
            int a=z*data.Width+x,b=a+data.Width,c=b+1,d=a+1;
            Indices[cursor++]=a; Indices[cursor++]=b; Indices[cursor++]=c;
            Indices[cursor++]=a; Indices[cursor++]=c; Indices[cursor++]=d;
        }
        Terrain=null;
    }

    public static CollisionGeometry Read(string path)
    {
        using var reader=new BinaryReader(File.OpenRead(path));
        if (reader.ReadUInt32()!=0x314d4b53) throw new IOException("Invalid collision cache header.");
        int vertices=reader.ReadInt32(),indices=reader.ReadInt32();
        if (vertices<=0 || vertices>2000000 || indices<=0 || indices>6000000 || indices%3!=0 ||
            reader.BaseStream.Length!=12L+vertices*12L+indices*4L)
            throw new IOException("Invalid collision buffer sizes.");
        var result=new CollisionGeometry {Vertices=new Vector3[vertices],Indices=new int[indices]};
        for (int i=0;i<vertices;i++)
        {
            float x=reader.ReadSingle(),y=reader.ReadSingle(),z=reader.ReadSingle();
            if (!float.IsFinite(x) || !float.IsFinite(y) || !float.IsFinite(z)) throw new IOException("Invalid collision vertex.");
            result.Vertices[i]=new Vector3(x,y,z);
        }
        for (int i=0;i<indices;i++)
        {
            int value=reader.ReadInt32();
            if (value<0 || value>=vertices) throw new IOException("Invalid collision index.");
            result.Indices[i]=value;
        }
        return result;
    }
}
sealed record TerrainSamples(float[] Heights,int Width,int Height,int X,int Z,float SpacingX,float SpacingZ,float ScaleY);

sealed record CollisionInstance(CollisionGeometry Geometry,Matrix4x4 Transform);

static class CollisionCrop
{
    public const float Radius=24;
    public static float[][][] Gather(IEnumerable<CollisionInstance> instances,Vector3 centre,Vector3 origin)
    {
        var triangles=new List<float[][]>();
        Gather(instances,centre,origin,(a,b,c)=>triangles.Add(new[]{new[]{a.X,a.Y,a.Z},new[]{b.X,b.Y,b.Z},new[]{c.X,c.Y,c.Z}}));
        return triangles.ToArray();
    }
    public static byte[] GatherPacked(IEnumerable<CollisionInstance> instances,Vector3 centre,Vector3 origin)
    {
        using var bytes=new MemoryStream();
        using var writer=new BinaryWriter(bytes);
        Gather(instances,centre,origin,(a,b,c)=>
        {
            writer.Write(a.X); writer.Write(a.Y); writer.Write(a.Z);
            writer.Write(b.X); writer.Write(b.Y); writer.Write(b.Z);
            writer.Write(c.X); writer.Write(c.Y); writer.Write(c.Z);
        });
        return bytes.ToArray();
    }
    private static void Gather(IEnumerable<CollisionInstance> instances,Vector3 centre,Vector3 origin,Action<Vector3,Vector3,Vector3> append)
    {
        int count=0;
        var min=centre-new Vector3(Radius,25,Radius);
        var max=centre+new Vector3(Radius,25,Radius);
        void Append(Vector3 a,Vector3 b,Vector3 c)
        {
            Vector3 Engine(Vector3 v) {v-=origin; return new Vector3(-v.X,v.Y,v.Z);}
            a=Engine(a); b=Engine(b); c=Engine(c);
            // Origin subtraction can collapse a world-space sliver; validate the transmitted floats with headroom.
            if (Vector3.Cross(c-a,b-a).LengthSquared()<1e-9f) return;
            if (count>=200000) throw new InvalidOperationException("Too much collision near the skateboard.");
            append(a,c,b); count++;
        }
        foreach (var instance in instances)
        {
            var geometry=instance.Geometry;
            geometry.Prepare();
            var world=new Vector3[geometry.Vertices.Length];
            for (int i=0;i<world.Length;i++) world[i]=Vector3.Transform(geometry.Vertices[i],instance.Transform);
            var indices=geometry.Indices;
            bool mirrored=instance.Transform.GetDeterminant()<0;
            for (int i=0;i<indices.Length;i+=3)
            {
                var a=world[indices[i]]; var b=world[indices[i+1]]; var c=world[indices[i+2]];
                if (mirrored) (b,c)=(c,b);
                if (CollisionClip.Outside(a,b,c,min,max)) continue;
                if (CollisionClip.Inside(a,min,max) && CollisionClip.Inside(b,min,max) && CollisionClip.Inside(c,min,max))
                    Append(a,b,c);
                else
                {
                    var polygon=CollisionClip.Polygon(a,b,c,min,max);
                    for (int j=1;j<polygon.Count-1;j++) Append(polygon[0],polygon[j],polygon[j+1]);
                }
            }
        }
        if (count==0) throw new InvalidOperationException("No usable ground collision was found.");
    }
}
