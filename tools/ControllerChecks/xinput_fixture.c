#include <stdint.h>
#include <string.h>

struct pad { uint16_t buttons; uint8_t lt, rt; int16_t lx, ly, rx, ry; };
struct state { uint32_t packet; struct pad pad; };
static int enabled;
void EnablePad(void) { enabled = 1; }
uint32_t XInputGetState(uint32_t index, struct state *state) {
    if (!enabled || index != 2) return 1167;
    memset(state, 0, sizeof(*state));
    state->pad = (struct pad){0x1000, 1, 255, -32768, 32767, 123, -456};
    return 0;
}
