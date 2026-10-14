using System.Net.Sockets;
using System.Text;
using Newtonsoft.Json;
using UnityEngine;

namespace SkateSchedule;

sealed class Reply
{
    public bool ok {get;set;}
    public string? error {get;set;}
    public float[] root = Array.Empty<float>();
    public float[][] bones = Array.Empty<float[]>();
    public string[] names = Array.Empty<string>();
    public float[][] rest = Array.Empty<float[]>();
    public float[] velocity = Array.Empty<float>();
    public float period {get;set;}
    public string state = "";
    public CameraPose? camera {get;set;}
    public bool collision_pending {get;set;}
    public ulong tick {get;set;}
    private bool interpolationFallbackLogged;
    public void Interpolate(Reply from,Reply to,float amount)
    {
        if (root.Length!=16) root=new float[16];
        if (bones.Length!=to.bones.Length) bones=to.bones.Select(_=>new float[16]).ToArray();
        void Fallback(string bone,float[] first,float[] next)
        {
            if (interpolationFallbackLogged) return;
            interpolationFallbackLogged=true;
            RuntimeDiagnostics.Record(new {kind="interpolation_fallback",bone,from_tick=from.tick,to_tick=to.tick,to.state,first,next});
        }
        if (!PoseBlend.Matrix(from.root,to.root,amount,root)) Fallback("root",from.root,to.root);
        for (int i=0;i<bones.Length;i++)
            if (!PoseBlend.Matrix(from.bones[i],to.bones[i],amount,bones[i])) Fallback(to.names[i],from.bones[i],to.bones[i]);
        names=to.names; state=to.state; tick=to.tick; period=to.period; velocity=to.velocity;
        if (from.camera!=null && to.camera!=null)
        {
            camera??=new CameraPose {position=new float[3],basis=new float[9]};
            for (int i=0;i<3;i++) camera.position[i]=from.camera.position[i]+(to.camera.position[i]-from.camera.position[i])*amount;
            PoseBlend.Basis(from.camera.basis,to.camera.basis,amount,camera.basis);
            camera.fov=from.camera.fov+(to.camera.fov-from.camera.fov)*amount;
        }
        else camera=to.camera;
    }

    public void Validate()
    {
        if (!ok) throw new IOException(error ?? "The skating engine rejected the request.");
        if (root.Length!=16 || velocity.Length!=3 || bones.Length!=names.Length ||
            root.Any(v=>!float.IsFinite(v)) || velocity.Any(v=>!float.IsFinite(v)) ||
            bones.Any(b=>b.Length!=16 || b.Any(v=>!float.IsFinite(v))) ||
            rest.Length!=0 && (rest.Length!=names.Length || rest.Any(b=>b.Length!=16 || b.Any(v=>!float.IsFinite(v)))) ||
            !float.IsFinite(period) || period<=0)
            throw new IOException("Invalid simulation pose.");
        if (camera!=null && (camera.position.Length!=3 || camera.basis.Length!=9 ||
            camera.position.Concat(camera.basis).Any(v=>!float.IsFinite(v)) || !float.IsFinite(camera.fov)))
            throw new IOException("Invalid simulation camera.");
        if (camera!=null) CameraMath.Validate(camera.basis);
    }
    public Matrix4x4 BoardPose()
    {
        int index=Array.IndexOf(names,"SKATEBOARD_ROOT");
        if (index<0) throw new IOException("The simulation has no skateboard bone.");
        return Space.Matrix(root)*Space.Matrix(bones[index]);
    }
}
sealed class CameraPose
{
    public float[] position=Array.Empty<float>();
    public float[] basis=Array.Empty<float>();
    public float fov {get;set;}
}
static class Space
{
    public static Vector3 FromEngine(float[] v) => new(-v[0],v[1],v[2]);
    public static float[] ToEngine(Vector3 v) => new[]{-v.x,v.y,v.z};
    public static Matrix4x4 Matrix(float[] columns)
    {
        return new Matrix4x4(new Vector4(columns[0],-columns[1],-columns[2],-columns[3]),
            new Vector4(-columns[4],columns[5],columns[6],columns[7]),
            new Vector4(-columns[8],columns[9],columns[10],columns[11]),
            new Vector4(-columns[12],columns[13],columns[14],1));
    }
}
sealed class BridgeConnection : IDisposable
{
    private readonly TcpClient client=new();
    private StreamReader? reader;
    private StreamWriter? writer;
    private static int capturedCollisions;
    public Reply Send(object request,bool capture=false,bool requireFrame=true)
    {
        string payload=JsonConvert.SerializeObject(request);
        if (capture)
        {
            // Retain the exact geometry/heading for headless replay; file work stays on this worker.
            string directory=Path.Combine(MelonLoader.Utils.MelonEnvironment.GameRootDirectory,"SkateScheduleDiagnostics");
            Directory.CreateDirectory(directory);
            string temporary=Path.Combine(directory,Guid.NewGuid()+".tmp");
            File.WriteAllText(temporary,payload);
            File.Move(temporary,Path.Combine(directory,"activation.json"),true);
        }
        return SendSerialized(payload,requireFrame:requireFrame);
    }
    public Reply SendPacked(object header,byte[] packed,bool capture=false)
    {
        if (packed.Length==0 || packed.Length%36!=0 || packed.Length/36>200000)
            throw new IOException("Invalid packed collision size.");
        string payload=JsonConvert.SerializeObject(header);
        if (capture)
        {
            string directory=Path.Combine(MelonLoader.Utils.MelonEnvironment.GameRootDirectory,"SkateScheduleDiagnostics");
            Directory.CreateDirectory(directory);
            string temporary=Path.Combine(directory,Guid.NewGuid()+".tmp");
            using (var file=File.Create(temporary))
            {
                var line=Encoding.UTF8.GetBytes(payload+"\n"); file.Write(line); file.Write(packed);
            }
            string operation=Newtonsoft.Json.Linq.JObject.Parse(payload)["op"]!.ToString();
            string filename=operation=="activate_packed"?$"activation-{DateTime.UtcNow.Ticks}.skb":$"collision-{DateTime.UtcNow.Ticks}-{Interlocked.Increment(ref capturedCollisions):D4}.skb";
            File.Move(temporary,Path.Combine(directory,filename),true);
            if (operation=="activate_packed") File.Copy(Path.Combine(directory,filename),Path.Combine(directory,"activation.skb"),true);
            foreach (var old in Directory.EnumerateFiles(directory,"*.skb").Where(f=>Path.GetFileName(f)!="activation.skb").OrderByDescending(File.GetLastWriteTimeUtc).Skip(64))
                File.Delete(old);
            RuntimeDiagnostics.Record(new {kind="geometry",file=filename,bytes=packed.Length,request=new Newtonsoft.Json.Linq.JRaw(payload)});
        }
        return SendSerialized(payload,packed);
    }
    public Reply SendSerialized(string payload,byte[]? packed=null,bool requireFrame=true)
    {
        var watch=System.Diagnostics.Stopwatch.StartNew();
        if (reader==null)
        {
            if (!client.ConnectAsync("127.0.0.1",31333).Wait(2000))
                throw new IOException("Start the native skate-schedule bridge first.");
            client.NoDelay=true;
            var stream=client.GetStream();
            stream.ReadTimeout=15000; stream.WriteTimeout=5000;
            reader=new StreamReader(stream,new UTF8Encoding(false),false,4096,leaveOpen:true);
            writer=new StreamWriter(stream,new UTF8Encoding(false),4096,leaveOpen:true) {AutoFlush=true};
        }
        writer!.WriteLine(payload);
        if (packed!=null) client.GetStream().Write(packed);
        // Bound the engine response while reading; never retain an unbounded line.
        var text=new StringBuilder();
        while (true)
        {
            int c=reader.Read();
            if (c<0) throw new IOException("The native bridge disconnected.");
            if (c=='\n') break;
            if (text.Length>=1024*1024) throw new IOException("Oversized simulation response.");
            text.Append((char)c);
        }
        var reply=JsonConvert.DeserializeObject<Reply>(text.ToString()) ?? throw new IOException("Empty simulation response.");
        if (!reply.ok)
        {
            try
            {
                string path=Path.Combine(MelonLoader.Utils.MelonEnvironment.GameRootDirectory,"SkateScheduleDiagnostics");
                Directory.CreateDirectory(path);
                File.WriteAllText(Path.Combine(path,"rejected-request.json"),payload);
                if (packed!=null) File.WriteAllBytes(Path.Combine(path,"rejected-request.bin"),packed);
                FailureCapture.Save(path,payload,text.ToString());
            }
            catch (Exception error) {MelonLoader.MelonLogger.Warning($"Could not capture rejected request: {error.Message}");}
        }
        RuntimeDiagnostics.Record(new {kind="reply",ms=watch.Elapsed.TotalMilliseconds,request=new Newtonsoft.Json.Linq.JRaw(payload),reply.tick,reply.state,reply.velocity,reply.root});
        if (requireFrame) reply.Validate();
        else if (!reply.ok) throw new IOException(reply.error ?? "The skating engine rejected the request.");
        return reply;
    }
    public void Dispose() {client.Dispose();}
}
