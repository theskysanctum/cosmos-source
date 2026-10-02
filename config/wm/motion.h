/* The motion settings of cosmos-wm, kept apart from the compositor so they can be tested on their own (test_motion.c). Pure math only - nothing here
 * touches wlroots, so it can be unit tested without a real compositor.
 *   animations = on|off    in ~/.config/cosmos/display.conf (later lines win), or COSMOS_ANIMATIONS=0|off in the environment (beats the file);
 *   COSMOS_ANIM_SCALE=N    stretches every animation N times (tests use it to look at the middle of one). */
#ifndef COSMOS_MOTION_H
#define COSMOS_MOTION_H
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ANIM_MS 500     /* how long a fade takes, both in and out */

static inline float __attribute__((unused)) ease_out_cubic(float t) {
	if (t < 0) {
		t = 0;
	}
	if (t > 1) {
		t = 1;
	}
	float u = 1.0f - t;
	return 1.0f - u * u * u;
}

static inline float __attribute__((unused)) lerp_float(float a, float b, float t) {
	return a + (b - a) * t;
}

/* on/true/yes/1 -> 1, off/false/no/0 -> 0, anything else -> fallback. */
static inline int __attribute__((unused)) parse_switch(const char *text, int fallback) {
	if (!text) {
		return fallback;
	}
	while (isspace((unsigned char)*text)) {
		text++;
	}
	char word[16] = "";
	size_t n = 0;
	while (text[n] && !isspace((unsigned char)text[n]) && n < sizeof(word) - 1) {
		word[n] = (char)tolower((unsigned char)text[n]);
		n++;
	}
	word[n] = 0;
	if (!strcmp(word, "on") || !strcmp(word, "true") || !strcmp(word, "yes") || !strcmp(word, "1")) {
		return 1;
	}
	if (!strcmp(word, "off") || !strcmp(word, "false") || !strcmp(word, "no") || !strcmp(word, "0")) {
		return 0;
	}
	return fallback;
}

static inline int __attribute__((unused)) animations_configured(void) {
	int on = 1;
	char path[512];
	const char *cfg = getenv("XDG_CONFIG_HOME");
	const char *home = getenv("HOME");
	if (cfg && *cfg) {
		snprintf(path, sizeof(path), "%s/cosmos/display.conf", cfg);
	} else if (home && *home) {
		snprintf(path, sizeof(path), "%s/.config/cosmos/display.conf", home);
	} else {
		path[0] = 0;
	}
	FILE *f = path[0] ? fopen(path, "r") : NULL;
	if (f) {
		char line[256], value[32];
		while (fgets(line, sizeof(line), f)) {
			if (sscanf(line, " animations = %31s", value) == 1) {
				on = parse_switch(value, on);
			}
		}
		fclose(f);
	}
	return parse_switch(getenv("COSMOS_ANIMATIONS"), on);       /* the environment beats the file */
}

static inline float __attribute__((unused)) animation_time_scale(void) {
	const char *env = getenv("COSMOS_ANIM_SCALE");
	float v = env ? (float)atof(env) : 1.0f;
	return (v >= 0.1f && v <= 100.0f) ? v : 1.0f;
}

#endif
