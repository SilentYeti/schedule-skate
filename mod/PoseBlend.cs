using System.Numerics;

namespace SkateSchedule;

static class PoseBlend
{
    public static bool Matrix(float[] from,float[] to,float amount,float[] output)
    {
        Matrix4x4 Read(float[] v) => new(v[0],v[1],v[2],0,v[4],v[5],v[6],0,v[8],v[9],v[10],0,v[12],v[13],v[14],1);
        if (!Matrix4x4.Decompose(Read(from),out var s0,out var q0,out var p0) ||
            !Matrix4x4.Decompose(Read(to),out var s1,out var q1,out var p1))
        {
            // Finite native animation matrices may contain shear; optional smoothing must not stop skating.
            Array.Copy(to,output,16); return false;
        }
        var m=Matrix4x4.CreateScale(Vector3.Lerp(s0,s1,amount))*Matrix4x4.CreateFromQuaternion(Quaternion.Slerp(q0,q1,amount))*
            Matrix4x4.CreateTranslation(Vector3.Lerp(p0,p1,amount));
        output[0]=m.M11; output[1]=m.M12; output[2]=m.M13; output[3]=0;
        output[4]=m.M21; output[5]=m.M22; output[6]=m.M23; output[7]=0;
        output[8]=m.M31; output[9]=m.M32; output[10]=m.M33; output[11]=0;
        output[12]=m.M41; output[13]=m.M42; output[14]=m.M43; output[15]=1;
        return true;
    }
    public static void Basis(float[] from,float[] to,float amount,float[] output)
    {
        Quaternion Read(float[] v) => Quaternion.CreateFromRotationMatrix(new Matrix4x4(v[0],v[1],v[2],0,v[3],v[4],v[5],0,v[6],v[7],v[8],0,0,0,0,1));
        var m=Matrix4x4.CreateFromQuaternion(Quaternion.Slerp(Read(from),Read(to),amount));
        output[0]=m.M11; output[1]=m.M12; output[2]=m.M13;
        output[3]=m.M21; output[4]=m.M22; output[5]=m.M23;
        output[6]=m.M31; output[7]=m.M32; output[8]=m.M33;
    }
}
