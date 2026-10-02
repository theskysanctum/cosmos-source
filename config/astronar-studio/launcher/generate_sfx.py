#!/usr/bin/env python3
"""Generates launcher/sounds/*.wav - the three UI SFX (move/confirm/
back) sfx.py plays via paplay. Plain synthesized tones (pure stdlib
math/wave/struct - no external assets, no licensing question), not real
recorded sound effects; run this by hand whenever a tone itself needs
tuning (pitch/length/volume/glide), same "keep the generator around, not
just its output" precedent as bezel/generate_bezels.py.

A real report that an earlier version of these (flat pure-sine tones,
a stepped two-note "power-up" rise for confirm, higher pitched, louder)
read as "retro" and "annoying" - fixed here by:

- A full raised-cosine (Hann) envelope across the *whole* tone, not just
  a short fade at each end - a plain linear fade-in/out still leaves the
  waveform's own slope discontinuous right at the fade points, which
  reads as a faint buzz/edge; a smooth bump from silence to peak and
  back removes that entirely, closer to a soft mallet tap than an
  electronic beep.
- A quiet second harmonic mixed under the fundamental - a lone sine is
  the exact timbre a chiptune/arcade blip uses; adding a touch of the
  octave above rounds it out instead.
- A gentle pitch glide (confirm rises, back falls) instead of confirm's
  old discrete two-note jump - a continuous glide reads as one soft
  gesture, not a little musical run.
- Lower registers (300-550 Hz, was up to 990 Hz) and lower volume than
  the original, retro-sounding version.

A real follow-up report that this version was too quiet to hear at all -
the envelope was the real culprit, not just the volume numbers: a raised-
cosine window across the *entire* tone only briefly grazes peak
amplitude at the very middle, so a short tone spends most of its length
well under its own nominal volume - quieter to the ear than the same
peak volume held for longer. Fixed with a real attack/sustain/release
envelope instead (still a smooth cosine rise/fall at each end, so it's
just as click-free as before) that actually holds at peak volume for the
middle of the tone, plus a real step up in the volume numbers themselves."""
import math
import struct
import wave
from pathlib import Path

SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"
SAMPLE_RATE = 44100
HARMONIC_MIX = 0.15  # how much quieter octave-up overtone gets mixed under the fundamental
ATTACK_FRAC = 0.2  # fraction of the tone's own length spent rising to peak
RELEASE_FRAC = 0.35  # fraction spent falling back to silence - longer than the attack so it trails off rather than cutting off


def _envelope(t):
	"""t in [0, 1] (how far through the tone this sample is). Smooth
	cosine rise for the first ATTACK_FRAC, a real plateau at full volume
	through the middle, then a smooth cosine fall for the last
	RELEASE_FRAC - unlike a window spanning the whole tone, this
	actually holds at peak long enough to be heard as that loud, not
	just brush past it for an instant."""
	if t < ATTACK_FRAC:
		return 0.5 - 0.5 * math.cos(math.pi * t / ATTACK_FRAC)
	if t > 1 - RELEASE_FRAC:
		return 0.5 - 0.5 * math.cos(math.pi * (1 - t) / RELEASE_FRAC)
	return 1.0


def _tone_samples(freq_start_hz, freq_end_hz, duration_s, volume):
	"""One tone, gliding linearly from freq_start_hz to freq_end_hz (equal
	for a flat pitch) with a quiet octave-up overtone and the attack/
	sustain/release envelope above. Phase is accumulated sample-by-sample
	(not computed directly from i/SAMPLE_RATE the way a fixed-frequency
	tone could be) since that's the correct way to synthesize a changing
	frequency without an audible phase discontinuity."""
	n = int(SAMPLE_RATE * duration_s)
	samples = []
	phase = 0.0
	phase2 = 0.0
	for i in range(n):
		t = i / (n - 1) if n > 1 else 0
		freq = freq_start_hz + (freq_end_hz - freq_start_hz) * t
		phase += 2 * math.pi * freq / SAMPLE_RATE
		phase2 += 2 * math.pi * (freq * 2) / SAMPLE_RATE
		value = (math.sin(phase) + HARMONIC_MIX * math.sin(phase2)) / (1 + HARMONIC_MIX)
		samples.append(volume * _envelope(t) * value)
	return samples


def _write(name, freq_start_hz, freq_end_hz, duration_s, volume):
	samples = _tone_samples(freq_start_hz, freq_end_hz, duration_s, volume)
	path = SOUNDS_DIR / name
	with wave.open(str(path), "wb") as f:
		f.setnchannels(1)
		f.setsampwidth(2)  # 16-bit
		f.setframerate(SAMPLE_RATE)
		f.writeframes(struct.pack(f"<{len(samples)}h", *(int(s * 32767) for s in samples)))
	print(f"wrote {path} ({len(samples) / SAMPLE_RATE * 1000:.0f}ms)")


def main():
	SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
	# Flat pitch, short - fires on every arrow-key move, so it's the one
	# sound that most needs to stay in the background rather than
	# announce itself - but still a real, audible tick, not near-silent.
	_write("move.wav", 480, 480, 0.045, 0.20)
	# A gentle upward glide reads as "accepted" without a discrete
	# musical run.
	_write("confirm.wav", 480, 580, 0.1, 0.26)
	# Mirrors confirm's glide downward instead of up, and pitched lower
	# than move/confirm - reads as "leaving/undoing".
	_write("back.wav", 380, 310, 0.09, 0.24)


if __name__ == "__main__":
	main()
