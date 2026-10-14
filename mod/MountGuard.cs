namespace SkateSchedule;

sealed class MountGuard
{
    private IntPtr protectedState;
    public IntPtr Begin(IntPtr localState,bool explicitDismount)
    {
        var previous=protectedState;
        protectedState=explicitDismount?IntPtr.Zero:localState;
        return previous;
    }
    public void End(IntPtr previous) => protectedState=previous;
    public bool AllowRemoval(IntPtr state) => state==IntPtr.Zero || state!=protectedState;
}
