/* The output scale policy of cosmos-wm, kept apart from the compositor so it can be tested on its own (test_output_scale.c). */
#ifndef COSMOS_OUTPUT_SCALE_H
#define COSMOS_OUTPUT_SCALE_H
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The compositor is the one place that decides how big things are on a screen (apps then only fit themselves into the logical size they are
 * given, see cosmos_display.py). Auto policy:
 *   - a monitor whose physical size is known and is up to 32 inches across the diagonal is scaled by its density (96 dpi is 1x);
 *   - a bigger screen (a TV, read from 10 feet away) or one that reports no size is scaled by its height against 1080, so a 4K TV shows a
 *     1920x1080 desktop drawn with four times the pixels;
 *   - scales are whole quarters, 1 below 1.25, and never so large that fewer than 1280 logical pixels are left across.
 * `scale = VALUE` (or `scale NAME = VALUE`) in ~/.config/cosmos/display.conf, or COSMOS_OUTPUT_SCALE, overrides it; `scale VALUE|auto` on the control
 * socket changes it until the output goes away. */

static inline double __attribute__((unused)) quantize_scale(double s) {
	if (s < 1.25) {
		return 1.0;
	}
	if (s > 3.0) {
		s = 3.0;
	}
	return floor(s * 4 + 0.5) / 4;
}

static inline double __attribute__((unused)) auto_scale(int width, int height, int phys_w_mm, int phys_h_mm) {
	double diagonal = (phys_w_mm > 0 && phys_h_mm > 0) ? hypot(phys_w_mm, phys_h_mm) / 25.4 : 0;
	double s;
	if (diagonal > 0 && diagonal <= 32.0) {
		s = width / (phys_w_mm / 25.4) / 96.0;
	} else {
		s = height / 1080.0;
	}
	s = quantize_scale(s);
	while (s > 1.0 && width / s < 1280) {
		s = s - 0.25 < 1.25 ? 1.0 : s - 0.25;
	}
	return s;
}

static inline double __attribute__((unused)) parse_scale(const char *text) {
	char *end = NULL;
	double v = strtod(text, &end);
	if (end == text || v < 0.5 || v > 4.0) {
		return 0;
	}
	return v;
}

/* The configured scale for an output (0 = automatic). */
static inline double __attribute__((unused)) configured_scale(const char *name) {
	double found = 0;
	const char *env = getenv("COSMOS_OUTPUT_SCALE");
	if (env && parse_scale(env) > 0) {
		found = parse_scale(env);
	}
	char path[512];
	const char *cfg = getenv("XDG_CONFIG_HOME");
	const char *home = getenv("HOME");
	if (cfg && *cfg) {
		snprintf(path, sizeof(path), "%s/cosmos/display.conf", cfg);
	} else if (home && *home) {
		snprintf(path, sizeof(path), "%s/.config/cosmos/display.conf", home);
	} else {
		return found;
	}
	FILE *f = fopen(path, "r");
	if (!f) {
		return found;
	}
	char line[256];
	while (fgets(line, sizeof(line), f)) {
		char key[128] = "", value[64] = "";
		if (sscanf(line, " scale %127[^= ] = %63s", key, value) == 2) {          /* scale NAME = VALUE */
			if (name && !strcmp(key, name) && parse_scale(value) > 0) {
				fclose(f);
				return parse_scale(value);
			}
		} else if (sscanf(line, " scale = %63s", value) == 1 && parse_scale(value) > 0) {
			found = parse_scale(value);
		}
	}
	fclose(f);
	return found;
}


/* ---- picking the video mode ---------------------------------------------------------------------------------------------------------------
 * A monitor's preferred mode is its native one, and that is what is used, unless it is absurdly small. Virtual GPUs (QEMU's virtio-vga) mark whatever
 * size their window happens to be as "preferred" - 640x480 right after the firmware's console - while listing every normal size after it. A preferred
 * mode below 1280x720 is therefore not trusted: the largest mode that fits 1920x1080 is used instead (60 Hz over other rates). A mode asked for in
 * display.conf (`mode = 1920x1080`, or `mode NAME = 1920x1080`) or COSMOS_OUTPUT_MODE beats all of it, when the output has that size. */

struct mode_info {
	int width, height;
	int refresh;         /* mHz */
	int preferred;
};

static inline int __attribute__((unused)) refresh_distance(int mhz) {
	return abs(mhz - 60000);
}

/* Index of the mode to use, or -1 when there are no modes. want_w/want_h are 0 unless a size was asked for. */
static inline int __attribute__((unused)) pick_mode(const struct mode_info *modes, int n, int want_w, int want_h) {
	int best = -1;
	if (n <= 0) {
		return -1;
	}
	if (want_w > 0 && want_h > 0) {
		for (int i = 0; i < n; i++) {
			if (modes[i].width == want_w && modes[i].height == want_h && (best < 0 || refresh_distance(modes[i].refresh) < refresh_distance(modes[best].refresh))) {
				best = i;
			}
		}
		if (best >= 0) {
			return best;
		}
	}
	for (int i = 0; i < n; i++) {
		if (modes[i].preferred && (long)modes[i].width * modes[i].height >= 1280L * 720) {
			return i;
		}
	}
	for (int i = 0; i < n; i++) {                        /* no trustworthy preferred mode: the largest that fits 1920x1080 */
		long area = (long)modes[i].width * modes[i].height;
		if (modes[i].width > 1920 || modes[i].height > 1080 || area < 1280L * 720) {
			continue;
		}
		if (best < 0) {
			best = i;
			continue;
		}
		long best_area = (long)modes[best].width * modes[best].height;
		if (area > best_area || (area == best_area && refresh_distance(modes[i].refresh) < refresh_distance(modes[best].refresh))) {
			best = i;
		}
	}
	if (best >= 0) {
		return best;
	}
	for (int i = 0; i < n; i++) {                        /* nothing suitable: the preferred one, or the largest of what there is */
		if (modes[i].preferred) {
			return i;
		}
	}
	best = 0;
	for (int i = 1; i < n; i++) {
		if ((long)modes[i].width * modes[i].height > (long)modes[best].width * modes[best].height) {
			best = i;
		}
	}
	return best;
}

/* "1920x1080" -> 1 and the size, else 0. */
static inline int __attribute__((unused)) parse_mode(const char *text, int *width, int *height) {
	int w = 0, h = 0;
	char extra = 0;
	if (sscanf(text, "%dx%d%c", &w, &h, &extra) != 2 || w < 320 || h < 200 || w > 16384 || h > 16384) {
		return 0;
	}
	*width = w;
	*height = h;
	return 1;
}

/* The configured mode for an output (false = automatic): COSMOS_OUTPUT_MODE, then `mode = WxH` and `mode NAME = WxH` in display.conf. */
static inline int __attribute__((unused)) configured_mode(const char *name, int *width, int *height) {
	int found = 0;
	const char *env = getenv("COSMOS_OUTPUT_MODE");
	if (env && parse_mode(env, width, height)) {
		found = 1;
	}
	char path[512];
	const char *cfg = getenv("XDG_CONFIG_HOME");
	const char *home = getenv("HOME");
	if (cfg && *cfg) {
		snprintf(path, sizeof(path), "%s/cosmos/display.conf", cfg);
	} else if (home && *home) {
		snprintf(path, sizeof(path), "%s/.config/cosmos/display.conf", home);
	} else {
		return found;
	}
	FILE *f = fopen(path, "r");
	if (!f) {
		return found;
	}
	char line[256];
	while (fgets(line, sizeof(line), f)) {
		char key[128] = "", value[64] = "";
		int w, h;
		if (sscanf(line, " mode %127[^= ] = %63s", key, value) == 2) {
			if (name && !strcmp(key, name) && parse_mode(value, &w, &h)) {
				fclose(f);
				*width = w;
				*height = h;
				return 1;
			}
		} else if (sscanf(line, " mode = %63s", value) == 1 && parse_mode(value, &w, &h)) {
			*width = w;
			*height = h;
			found = 1;
		}
	}
	fclose(f);
	return found;
}

#endif
