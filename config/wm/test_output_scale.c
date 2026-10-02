/* Tests for the output scale policy: cc -o t test_output_scale.c -lm && ./t */
#include <assert.h>
#include "output_scale.h"

static void expect(const char *what, double got, double want) {
	if (got != want) {
		printf("FAIL %s: got %.2f, want %.2f\n", what, got, want);
		exit(1);
	}
	printf("ok   %s = %.2f\n", what, got);
}

static int pick(const struct mode_info *m, int n, int w, int h) { return pick_mode(m, n, w, h); }

/* the modes QEMU's virtio-vga reported to the guest (from a real run's journal), preferred flag and all */
static const struct mode_info VIRTIO[] = {
	{640, 480, 143999, 1}, {5120, 2160, 50000, 0}, {3840, 2160, 60000, 0}, {1920, 1440, 60000, 0}, {2048, 1152, 60000, 0}, {1920, 1200, 59885, 0},
	{1920, 1080, 60000, 0}, {1920, 1080, 50000, 0}, {1600, 1200, 60000, 0}, {1280, 1024, 60020, 0}, {1024, 768, 60004, 0}, {640, 480, 60000, 0},
};
#define VIRTIO_N ((int)(sizeof(VIRTIO) / sizeof(VIRTIO[0])))

static void expect_mode(const char *what, int index, int want_w, int want_h, int want_hz_mhz) {
	if (index < 0 || VIRTIO[index].width != want_w || VIRTIO[index].height != want_h || VIRTIO[index].refresh != want_hz_mhz) {
		printf("FAIL %s: got %dx%d@%d\n", what, index < 0 ? -1 : VIRTIO[index].width, index < 0 ? -1 : VIRTIO[index].height, index < 0 ? -1 : VIRTIO[index].refresh);
		exit(1);
	}
	printf("ok   %s = %dx%d@%d\n", what, want_w, want_h, want_hz_mhz);
}

int main(void) {
	expect("1080p, no size reported", auto_scale(1920, 1080, 0, 0), 1.0);
	expect("720p, no size reported", auto_scale(1280, 720, 0, 0), 1.0);
	expect("4K, no size reported (a TV)", auto_scale(3840, 2160, 0, 0), 2.0);
	expect("4K 55in TV", auto_scale(3840, 2160, 1210, 680), 2.0);
	expect("1440p, no size reported", auto_scale(2560, 1440, 0, 0), 1.25);
	expect("1080p 24in monitor", auto_scale(1920, 1080, 531, 299), 1.0);
	expect("1440p 27in monitor", auto_scale(2560, 1440, 597, 336), 1.0);
	expect("4K 27in monitor (163 dpi)", auto_scale(3840, 2160, 597, 336), 1.75);
	expect("4K 15.6in laptop", auto_scale(3840, 2160, 344, 194), 3.0);
	expect("1080p 13in laptop (166 dpi wants 1.75; 1.5 keeps 1280 logical)", auto_scale(1920, 1080, 294, 165), 1.5);
	expect("1366x768 laptop", auto_scale(1366, 768, 309, 174), 1.0);
	expect("a tiny dense screen still leaves 1280 logical pixels", auto_scale(1920, 1080, 200, 112), 1.5);
	expect("parse 1.5", parse_scale("1.5"), 1.5);
	expect("parse auto is not a number", parse_scale("auto"), 0);
	expect("parse too big", parse_scale("9"), 0);
	expect("quantize 1.1", quantize_scale(1.1), 1.0);
	expect("quantize 1.37", quantize_scale(1.37), 1.25);
	expect("quantize 5", quantize_scale(5), 3.0);

	expect_mode("virtio-vga: a 640x480 'preferred' mode is not trusted; 1920x1080@60 is used", pick(VIRTIO, VIRTIO_N, 0, 0), 1920, 1080, 60000);
	expect_mode("a size asked for wins (1280x1024)", pick(VIRTIO, VIRTIO_N, 1280, 1024), 1280, 1024, 60020);
	expect_mode("a size that does not exist is ignored", pick(VIRTIO, VIRTIO_N, 1234, 567), 1920, 1080, 60000);
	expect_mode("asked-for size takes the rate nearest 60 Hz", pick(VIRTIO, VIRTIO_N, 1920, 1080), 1920, 1080, 60000);
	{
		struct mode_info monitor[] = {{3840, 2160, 60000, 1}, {3840, 2160, 30000, 0}, {1920, 1080, 60000, 0}};
		int i = pick_mode(monitor, 3, 0, 0);
		expect("a monitor's native 4K preferred mode is kept", monitor[i].width * 1.0, 3840);
	}
	{
		struct mode_info old_panel[] = {{1024, 768, 60000, 1}, {800, 600, 60000, 0}};
		int i = pick_mode(old_panel, 2, 0, 0);
		expect("a genuinely small screen keeps its own preferred mode", old_panel[i].width * 1.0, 1024);
	}
	{
		struct mode_info none[] = {{0, 0, 0, 0}};
		expect("no modes at all", pick_mode(none, 0, 0, 0), -1);
	}
	{
		int w = 0, h = 0;
		expect("parse 1920x1080", parse_mode("1920x1080", &w, &h) * 1.0 + w - 1920, 1);
		expect("parse rejects junk", parse_mode("big", &w, &h), 0);
		expect("parse rejects a trailing suffix", parse_mode("1920x1080p", &w, &h), 0);
	}
	return 0;
}
