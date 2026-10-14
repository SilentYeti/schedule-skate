using System.Runtime.InteropServices;
using UnityEngine;

namespace SkateSchedule;

static class Controller
{
    internal static bool PreferKeyboard;
    private static Pad keyboard;
    private static ushort pressed;
    private static float mouseX,mouseY;
    private static bool mouseActive;
    [StructLayout(LayoutKind.Sequential)]
    private struct Pad {public ushort Buttons; public byte LeftTrigger,RightTrigger; public short LX,LY,RX,RY;}
    [StructLayout(LayoutKind.Sequential)]
    private struct State {public uint Packet; public Pad Pad;}
    [DllImport("xinput1_4.dll",EntryPoint="XInputGetState")]
    private static extern uint GetState(uint index,out State state);

    internal static void Reset()
    {
        keyboard=default; pressed=0; mouseX=mouseY=0; mouseActive=false;
    }

    // Poll on every rendered frame; bridge requests can skip frames while awaiting a reply.
    internal static void Poll(float sensitivity=0.08f)
    {
        sensitivity=float.IsFinite(sensitivity)?Math.Clamp(sensitivity,0.001f,1f):0.08f;
        bool Key(KeyCode key) => Input.GetKey(key);
        float Axis(KeyCode positive,KeyCode negative) => (Key(positive)?1:0)-(Key(negative)?1:0);
        ushort buttons=0;
        foreach (var (key,mask) in Keys) if (Key(key)) buttons|=mask;
        if (Input.GetMouseButton(2)) buttons|=0x80;
        pressed|=buttons;
        bool alt=Key(KeyCode.LeftAlt)||Key(KeyCode.RightAlt);
        if (alt && mouseActive)
        {
            mouseX+=Input.GetAxisRaw("Mouse X")*sensitivity;
            mouseY+=Input.GetAxisRaw("Mouse Y")*sensitivity;
            float length=MathF.Sqrt(mouseX*mouseX+mouseY*mouseY);
            if (length>1) {mouseX/=length; mouseY/=length;}
        }
        else mouseX=mouseY=0;
        mouseActive=alt;
        float rx=Axis(KeyCode.RightArrow,KeyCode.LeftArrow),ry=Axis(KeyCode.UpArrow,KeyCode.DownArrow);
        if (rx==0 && ry==0 && alt) {rx=mouseX; ry=mouseY;}
        float amount=Key(KeyCode.LeftControl)||Key(KeyCode.RightControl)?0.5f:1f;
        keyboard=new Pad {Buttons=buttons,LeftTrigger=(byte)(Input.GetMouseButton(1)||Key(KeyCode.Z)?255:0),
            RightTrigger=(byte)(Input.GetMouseButton(0)||Key(KeyCode.X)?255:0),
            LX=(short)(Axis(KeyCode.D,KeyCode.A)*32767),LY=(short)(Axis(KeyCode.W,KeyCode.S)*32767),
            RX=(short)(rx*amount*32767),RY=(short)(ry*amount*32767)};
    }

    private static readonly (KeyCode key,ushort mask)[] Keys={
        (KeyCode.Space,0x1000),(KeyCode.C,0x2000),(KeyCode.E,0x4000),(KeyCode.F,0x8000),
        (KeyCode.Q,0x100),(KeyCode.R,0x200),(KeyCode.LeftShift,0x40),(KeyCode.RightShift,0x40),
        (KeyCode.Tab,0x20),(KeyCode.Return,0x10),
        (KeyCode.I,1),(KeyCode.K,2),(KeyCode.J,4),(KeyCode.L,8),
    };

    public static object Step(byte ticks,float aspect=16f/9f)
    {
        Pad p=keyboard;
        p.Buttons|=pressed;
        pressed=0;
        for (uint i=0;!PreferKeyboard && i<4;i++)
        {
            if (GetState(i,out var state)==0)
            {
                p=state.Pad;
                break;
            }
        }
        // Json.NET encodes byte[] as Base64; the bridge requires two JSON numbers.
        return new {op="step",buttons=p.Buttons,triggers=new int[]{p.LeftTrigger,p.RightTrigger},
            left=new[]{p.LX,p.LY},right=new[]{p.RX,p.RY},ticks,aspect};
    }
}
