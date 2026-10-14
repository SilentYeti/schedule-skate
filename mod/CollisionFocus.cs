using System.Numerics;

namespace SkateSchedule;

static class CollisionFocus
{
    public static bool Detached(string state) => state.StartsWith("Wipeout",StringComparison.Ordinal) ||
        state is "Teleporting" or "BipedGround" or "BipedAir" or "OffBoardPushing";
    public static Vector3 Position(string state,Vector3 board,Vector3 rider) => Detached(state)?rider:board;
}
