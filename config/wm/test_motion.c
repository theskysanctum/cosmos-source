/* Tests for the motion settings: cc -o t test_motion.c && ./t */
#include <assert.h>
#include <unistd.h>
#include "motion.h"

static void check(const char *what, int ok) {
	if (!ok) {
		printf("FAIL %s\n", what);
		exit(1);
	}
	printf("ok   %s\n", what);
}

int main(void) {
	check("ANIM_MS is half a second", ANIM_MS == 500);
	check("easing starts at 0", ease_out_cubic(0) == 0);
	check("easing ends at 1", ease_out_cubic(1) == 1);
	check("easing is ahead of linear at the start", ease_out_cubic(0.25f) > 0.25f);
	check("easing is clamped below 0", ease_out_cubic(-3) == 0);
	check("easing is clamped above 1", ease_out_cubic(9) == 1);
	check("lerp", lerp_float(2, 4, 0.5f) == 3);
	check("lerp reversed (fade out)", lerp_float(1, 0, 0.25f) == 0.75f);
	check("on", parse_switch("on", 0) == 1);
	check("Off with spaces and capitals", parse_switch("  OFF ", 1) == 0);
	check("junk keeps the fallback", parse_switch("maybe", 1) == 1 && parse_switch("maybe", 0) == 0);
	check("NULL keeps the fallback", parse_switch(NULL, 1) == 1);

	setenv("HOME", "/nonexistent", 1);
	unsetenv("XDG_CONFIG_HOME");
	unsetenv("COSMOS_ANIMATIONS");
	check("on by default", animations_configured() == 1);
	setenv("COSMOS_ANIMATIONS", "off", 1);
	check("the environment turns them off", animations_configured() == 0);
	setenv("COSMOS_ANIMATIONS", "junk", 1);
	check("a junk environment value changes nothing", animations_configured() == 1);
	unsetenv("COSMOS_ANIMATIONS");

	char dir[] = "/home/user/.placements/tests/motion-XXXXXX";
	system("mkdir -p /home/user/.placements/tests");
	if (!mkdtemp(dir)) {
		return 1;
	}
	char cmd[512];
	snprintf(cmd, sizeof(cmd), "mkdir -p %s/cosmos && printf 'scale = 1.5\\nanimations = off\\n' > %s/cosmos/display.conf", dir, dir);
	system(cmd);
	setenv("XDG_CONFIG_HOME", dir, 1);
	check("display.conf turns them off", animations_configured() == 0);
	setenv("COSMOS_ANIMATIONS", "on", 1);
	check("the environment beats display.conf", animations_configured() == 1);
	snprintf(cmd, sizeof(cmd), "rm -rf %s", dir);
	system(cmd);

	unsetenv("COSMOS_ANIM_SCALE");
	check("time scale defaults to 1", animation_time_scale() == 1.0f);
	setenv("COSMOS_ANIM_SCALE", "20", 1);
	check("time scale from the environment", animation_time_scale() == 20.0f);
	setenv("COSMOS_ANIM_SCALE", "0", 1);
	check("a silly time scale is ignored", animation_time_scale() == 1.0f);
	return 0;
}
