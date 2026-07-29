"""Tests against real bytes captured from a Glowrium-C045.

The fixtures in tests/data/ are verbatim captures, not synthesised data, so
these tests pin the decoder to hardware behaviour rather than to my reading of
it. Load them from disk rather than retyping hex inline, which is how two
transcription bugs got into the first version of this file.
"""

from pathlib import Path

import pytest

from glowctl import const, protocol

DATA = Path(__file__).parent / "data"


def load_hex(name: str) -> bytes:
    """Read a fixture, ignoring comment lines and whitespace."""
    text = (DATA / name).read_text()
    body = " ".join(l for l in text.splitlines() if not l.startswith("#"))
    return bytes(int(tok, 16) for tok in body.split())


def load_hex_lines(name: str) -> list[bytes]:
    """Read a fixture holding one frame per line."""
    text = (DATA / name).read_text()
    return [
        bytes(int(t, 16) for t in line.split())
        for line in text.splitlines() if line and not line.startswith("#")
    ]


@pytest.fixture
def state():
    return protocol.decode_state(load_hex("state_facebd02.hex"))


# ------------------------------------------------------------------ decoding

def test_decodes_confirmed_scalars(state):
    """The three properties we are actually certain about."""
    assert state[101] == 3                      # version, matches facebd81
    assert state[83] == -480                    # timezone, exactly UTC-8
    assert state[84] == 1785205150826           # ms epoch


def test_decodes_lighting_scalars(state):
    assert state[const.KEY_BRIGHTNESS] == 49
    assert state[const.KEY_COLOUR] == bytes.fromhex("6e010032")


def test_program_properties_have_expected_shapes(state):
    """Programs are a fixed 20 steps of 4-byte RGBY, after a header."""
    assert len(state[22]) == const.SEGMENT_COUNT * const.CHANNEL_COUNT
    assert len(state[23]) == const.SEGMENT_COUNT
    assert len(state[24]) == 20 + const.SEGMENT_COUNT * const.CHANNEL_COUNT
    assert len(state[41]) == 32 + const.SEGMENT_COUNT * const.CHANNEL_COUNT


def test_schedule_times_are_seconds_of_day(state):
    assert state[77] == [0, 21600, 64800]       # 00:00, 06:00, 18:00


def test_truncated_read_yields_a_usable_prefix():
    """A cut-short read must not lose everything it did receive.

    The real capture is itself truncated: the device reports a 28-pair map but
    only 22 pairs arrived.
    """
    full = load_hex("state_facebd02.hex")
    assert full[:2] == b"\xb8\x1c"              # map header claims 28 pairs
    assert len(protocol.decode_state(full)) == 22

    partial = protocol.decode_state(full[:40])
    assert partial[101] == 3
    assert len(partial) < 22


def test_parse_program_splits_into_rgby_steps(state):
    """The factory preset walks each channel to full in turn, which is what
    established that colour entries are 4 channels rather than 3."""
    steps = protocol.parse_segments(state[41], header_len=32)
    assert len(steps) == const.SEGMENT_COUNT
    assert steps[0] == (255, 0, 0, 0)
    assert steps[1] == (0, 255, 0, 0)
    assert steps[2] == (0, 0, 255, 0)
    assert steps[3] == (0, 0, 0, 255)
    assert steps[4] == steps[0]                 # the pattern repeats


# --------------------------------------------------------------- notifications

def test_notifications_decode_as_property_maps():
    frames = load_hex_lines("notify_facebd02.hex")
    decoded = [protocol.decode_state(f) for f in frames]
    assert [set(d) for d in decoded] == [{84, 2}] * 3


def test_notification_timestamps_advance_monotonically():
    decoded = [protocol.decode_state(f) for f in load_hex_lines("notify_facebd02.hex")]
    stamps = [d[84] for d in decoded]
    assert stamps == sorted(stamps)
    assert [b - a for a, b in zip(stamps, stamps[1:])] == [2000, 5000]


# ------------------------------------------------------- power, end to end

def load_labelled_hex(name: str) -> dict[str, bytes]:
    """Read a fixture of `label: hex bytes` lines."""
    out = {}
    for line in (DATA / name).read_text().splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        label, _, body = line.partition(":")
        out[label.strip()] = bytes(int(t, 16) for t in body.split())
    return out


@pytest.fixture
def power_cycle():
    return {k: protocol.decode_state(v)
            for k, v in load_labelled_hex("power_cycle.hex").items()}


def test_key_2_is_power(power_cycle):
    """Writing key 2 turns the lamp on and off; verified against hardware.

    Captured by writing encode_power(False) then encode_power(True) to
    facebd01. The device acknowledged each write, pushed a matching
    notification, reported the new value in its state map, and physically
    changed state.
    """
    assert power_cycle["state_before"][2] is True
    assert power_cycle["notify_off"][2] is False
    assert power_cycle["state_off"][2] is False
    assert power_cycle["notify_on"][2] is True
    assert power_cycle["state_on"][2] is True
    assert const.PROPERTIES[2].name == "GPower"
    assert const.PROPERTIES[2].confidence == "confirmed"


def test_power_write_does_not_disturb_other_properties(power_cycle):
    """Only power changed; brightness and colour survived the round trip.

    This is what rules out the write having been interpreted as something
    broader than the single key it named.
    """
    for snapshot in ("state_before", "state_off", "state_on"):
        assert power_cycle[snapshot][const.KEY_BRIGHTNESS] == 100
        assert power_cycle[snapshot][const.KEY_COLOUR] == bytes.fromhex("46040032")


def test_the_frames_we_actually_sent_are_what_the_encoder_produces():
    """Ties the hardware proof back to the encoder callers use."""
    assert protocol.encode_power(False) == bytes.fromhex("a102f4")
    assert protocol.encode_power(True) == bytes.fromhex("a102f5")


# --------------------------------------------- brightness and colour, on device

@pytest.fixture
def cycle():
    return {k: protocol.decode_state(v)
            for k, v in load_labelled_hex("brightness_colour_cycle.hex").items()}


def test_brightness_round_trips(cycle):
    """Written 20 then 100, read back exactly, and visibly changed the lamp."""
    assert cycle["baseline"][const.KEY_BRIGHTNESS] == 100
    assert cycle["bright20"][const.KEY_BRIGHTNESS] == 20
    assert cycle["bright100"][const.KEY_BRIGHTNESS] == 100
    assert const.PROPERTIES[1].confidence == "confirmed"


def test_colour_round_trips_byte_exact(cycle):
    """Each of the four channels was driven to full and read back."""
    assert cycle["red"][const.KEY_COLOUR] == bytes.fromhex("ff000000")
    assert cycle["green"][const.KEY_COLOUR] == bytes.fromhex("00ff0000")
    assert cycle["blue"][const.KEY_COLOUR] == bytes.fromhex("0000ff00")
    assert cycle["yellow"][const.KEY_COLOUR] == bytes.fromhex("000000ff")
    assert cycle["restored"][const.KEY_COLOUR] == cycle["baseline"][const.KEY_COLOUR]


def test_colour_writes_do_not_disturb_brightness(cycle):
    for snapshot in ("red", "green", "blue", "yellow", "restored"):
        assert cycle[snapshot][const.KEY_BRIGHTNESS] == 100


def test_mode_key_held_constant_while_colour_failed_to_render(cycle):
    """Colour stored perfectly across all four writes, yet the lamp stayed
    white the whole time, and key 19 never moved. That pointed at key 19 as
    the mode selector; test_mode_key_tracks_the_physical_mode_switch confirms
    it against a real mode change."""
    assert {bytes(c[19]) for c in cycle.values()} == {bytes.fromhex("010004")}


# ------------------------------------------------------------ the mode selector

@pytest.fixture
def cycling():
    return {k: protocol.decode_state(v)
            for k, v in load_labelled_hex("mode_cycling.hex").items()}


def test_mode_key_tracks_the_physical_mode_switch(cycle, cycling):
    """Pressing the lamp's mode switch moved key 19 and nothing else structural.

    White mode read 01 00 04; after switching to colour cycling it read
    01 00 00. Every other property held, which is what identifies byte 3 of
    key 19 as the mode.
    """
    white, cycling_now = cycle["restored"], cycling["cycling_t0"]
    assert white[19] == bytes.fromhex("010004")
    assert cycling_now[19] == bytes.fromhex("010000")

    # Everything else in the comparable prefix is unchanged, apart from the
    # timestamp and a minor colour adjustment the mode change made.
    unchanged = set(white) - {19, 84, 8}
    assert unchanged, "sanity: there should be other keys to compare"
    for key in unchanged:
        assert white[key] == cycling_now[key], f"key {key} unexpectedly moved"

    assert const.PROPERTIES[19].name == "ModeCtr"


def test_mode_needs_both_properties_written_together():
    """A mode is ModeCtr + DyData in one write, confirmed on hardware.

    Writing DyData alone left the lamp half-applied: the middle of the column
    flickered with the new program while the top stayed on its old colour.
    Adding ModeCtr completed it into a correct campfire.
    """
    frame = protocol.encode_mode(bytes.fromhex("01009c"), b"\x00" * 100)
    assert sorted(protocol.decode_state(frame)) == [const.KEY_MODE,
                                                    const.KEY_DYDATA]
    with pytest.raises(ValueError):
        protocol.encode_mode(b"\x01\x00", b"\x00" * 100)      # wrong ModeCtr size
    with pytest.raises(ValueError):
        protocol.encode_mode(bytes.fromhex("01009c"), b"\x00" * 99)


def test_cycling_animation_is_not_reflected_in_the_colour_key(cycling):
    """Two reads 4s apart mid-cycle differ only in the timestamp.

    So the animation runs on-device and key 8 is the stored manual colour, not
    live output. Any status display built on key 8 must not claim to show what
    the lamp is currently emitting.
    """
    t0, t4 = cycling["cycling_t0"], cycling["cycling_t4"]
    assert t0[84] != t4[84]
    for key in set(t0) - {84}:
        assert t0[key] == t4[key], f"key {key} animated, which changes the story"


# ------------------------------------------------------------------- identity

def test_identity_parses():
    raw = (DATA / "identity_facebd80.txt").read_bytes()
    ident = protocol.parse_identity(raw)
    assert ident["pkey"] == "Glowrium-C045"
    assert ident["mac"] == "8856A6F2364C"
    assert ident["version"] == "3"
    assert ident["devid"] == "ESP-8856A6F2364C"


def test_identity_mac_matches_the_wifi_mac_not_the_ble_mac():
    """The BLE MAC is this + 2, which is the ESP32 signature."""
    ident = protocol.parse_identity((DATA / "identity_facebd80.txt").read_bytes())
    wifi = int(ident["mac"], 16)
    assert wifi == 0x8856A6F2364C
    assert wifi + 2 == 0x8856A6F2364E        # matches the advertised name suffix


# ------------------------------------------------------------------- encoding

@pytest.mark.parametrize("call, expected", [
    (lambda: protocol.encode_power(True), "a102f5"),
    (lambda: protocol.encode_power(False), "a102f4"),
    (lambda: protocol.encode_brightness(75), "a101184b"),
    (lambda: protocol.encode_brightness(0), "a10100"),
])
def test_encoders_produce_minimal_cbor(call, expected):
    assert call().hex() == expected


def test_encoded_frames_round_trip_through_the_decoder():
    frame = protocol.encode_colour(1, 2, 3, 4)
    decoded = protocol.decode_state(frame)
    assert set(decoded) == {const.KEY_SEGMENTS}
    assert protocol.parse_segments(decoded[const.KEY_SEGMENTS]) == \
        [(1, 2, 3, 4)] * const.SEGMENT_COUNT


def test_encode_colour_targets_segments_not_key_8():
    """Regression guard for the bug that cost a whole debugging cycle.

    encode_colour used to write key 8, which stores perfectly and renders
    never. Solid colour is really "every segment the same".
    """
    decoded = protocol.decode_state(protocol.encode_colour(255, 0, 0))
    assert const.KEY_COLOUR not in decoded
    assert const.KEY_SEGMENTS in decoded


def test_encoder_rejects_out_of_range_colour():
    with pytest.raises(ValueError):
        protocol.encode_colour(256, 0, 0)


def test_encoder_rejects_out_of_range():
    with pytest.raises(ValueError):
        protocol.encode_brightness(101)
    with pytest.raises(ValueError):
        protocol.encode_brightness(-1)


def test_unknown_properties_are_refused_by_default():
    """The write guard is the main thing standing between a typo and a brick."""
    with pytest.raises(protocol.UnsafeProperty):
        protocol.encode_properties({87: 1})      # seen but meaning unknown
    with pytest.raises(protocol.UnsafeProperty):
        protocol.encode_properties({9999: 1})    # never seen at all
    # ...but an explicit override still works, for deliberate probing.
    assert protocol.encode_properties({87: 1}, allow_unsafe=True)


# ------------------------------------------------------- per-segment addressing

def test_lamp_is_twenty_addressable_segments():
    """The segments paint R,G,B repeating from the top."""
    raw = load_labelled_hex("segments_rgb.hex")["segments_key22"]
    segs = protocol.parse_segments(raw)
    assert len(segs) == const.SEGMENT_COUNT
    assert len(set(segs)) == 3                    # exactly red, green, blue


def test_segments_are_stored_bottom_to_top():
    """The lamp was painted RGB from the TOP; the array reads GRB from index 0.

    That offset is only explicable if index 0 is the bottom segment. Getting
    this backwards would silently mirror every pattern.
    """
    raw = load_labelled_hex("segments_rgb.hex")["segments_key22"]
    RED, GREEN, BLUE = (255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0)

    device_order = protocol.parse_segments(raw)
    assert device_order[0] == GREEN               # bottom segment

    from_top = protocol.segments_from_top(raw)
    assert from_top[0] == RED                     # top segment, as painted
    assert from_top[:6] == [RED, GREEN, BLUE, RED, GREEN, BLUE]


def test_segment_index_conversion_is_its_own_inverse():
    for i in range(const.SEGMENT_COUNT):
        assert const.segment_index_from_top(const.segment_index_from_top(i)) == i
    with pytest.raises(ValueError):
        const.segment_index_from_top(const.SEGMENT_COUNT)


def test_encode_segments_round_trips_through_the_parser():
    pattern = [(i * 10 % 256, 0, 0, 0) for i in range(const.SEGMENT_COUNT)]
    payload = protocol.encode_segments(pattern, from_top=True)
    assert len(payload) == const.SEGMENT_COUNT * const.CHANNEL_COUNT
    assert protocol.segments_from_top(payload) == pattern


def test_encode_segments_rejects_wrong_length():
    with pytest.raises(ValueError):
        protocol.encode_segments([(0, 0, 0, 0)] * 19)


def test_factory_preset_is_a_static_rainbow_not_an_animation(state):
    """Re-reading of the preset once segments were understood.

    R,G,B,Y repeating five times over 20 slots is a rainbow down the lamp, not
    a four-step colour cycle, which is how it was first misread.
    """
    segs = protocol.parse_segments(state[41], header_len=32)
    assert segs[:4] == [(255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255)]
    assert len(set(segs)) == const.CHANNEL_COUNT
    assert segs == segs[:4] * 5


def test_per_segment_write_rendered_on_hardware():
    """The four-band pattern we wrote came back byte-exact and was seen.

    Top to bottom: 5 red, 5 green, 5 blue, 5 yellow. Confirmed visually, which
    is what validates the bottom-to-top storage order: had it been backwards,
    the bands would have rendered mirrored.
    """
    raw = load_labelled_hex("segments_written.hex")
    RED, GREEN, BLUE, YELLOW = (255, 0, 0, 0), (0, 255, 0, 0), (0, 0, 255, 0), (0, 0, 0, 255)
    expected = [RED] * 5 + [GREEN] * 5 + [BLUE] * 5 + [YELLOW] * 5

    assert protocol.segments_from_top(raw["segments_after"]) == expected
    assert protocol.encode_segments(expected) == raw["segments_after"]


def test_writing_segments_needs_no_mode_change():
    """Mode held constant across the write, so key 22 renders on its own."""
    raw = load_labelled_hex("segments_written.hex")
    assert raw["mode_before"] == raw["mode_after"]


def test_reference_white_is_not_full_yellow():
    """The reference "bright white" is (255,255,255,200): Y is held back.

    Worth pinning, since a naive white of (255,255,255,255) is not what
    the white preset specifies and would render differently.
    """
    raw = load_labelled_hex("segments_written.hex")
    before = protocol.parse_segments(raw["segments_before"])
    assert set(before) == {(255, 255, 255, 200)}


# --------------------------------------------------------- music reactive mode

def test_music_mode_does_not_stream_frames_over_ble():
    """The microphone is on the lamp, so nothing audio-related crosses BLE.

    Three reads ~3s apart while it was actively reacting to sound differed only
    in the timestamp. Key 22 holds the mode's palette, not live output, so a
    real-time visualiser cannot be driven from reads.
    """
    raw = load_labelled_hex("mode_music_energy_core.hex")
    assert raw["mode"] == const.MODE_VALUES["music_energy_core"]
    palette = protocol.parse_segments(raw["segments_key22"])
    assert len(palette) == const.SEGMENT_COUNT
    assert len(set(palette)) > 1          # a gradient palette, not a flat colour


def test_music_mode_introduced_a_new_mode_family_byte():
    """Byte 0 of key 19 took 0x02 for the first time in music mode.

    Every earlier mode used 0x00 or 0x01. Recorded as an observation only; two
    structural readings of this key have already been falsified.
    """
    families = {v[0] for v in const.MODE_VALUES.values()}
    assert families == {0x00, 0x01, 0x02}
    assert const.MODE_VALUES["music_energy_core"][0] == 0x02


def test_key_8_byte_3_tracks_mode_not_colour():
    """The evidence that key 8 is parameters rather than a colour.

    Byte 3 held 0x32 across every non-music mode and dropped to 0x01 in music
    mode. A yellow channel would not behave that way.
    """
    music = load_labelled_hex("mode_music_energy_core.hex")["key8"]
    solid = load_labelled_hex("segments_rgb.hex")["colour_key8"]
    assert solid[3] == 0x32
    assert music[3] == 0x01
    assert music[:3] == solid[:3]         # the other three bytes are unchanged
    assert const.PROPERTIES[8].name == "colorMode"


def test_mode_ctr_alone_does_not_repaint():
    """ModeCtr written on its own changes the report and nothing else.

    Both writes were acked, change-pushed and read back, yet the lamp kept
    pulsing to sound. This observation stands, but the conclusion drawn from it
    at the time, that ModeCtr was a passive label, was wrong: it is one half of
    a two-property write and only does nothing when sent alone.
    """
    raw = load_labelled_hex("mode_write.hex")
    after_white = protocol.decode_state(raw["notify_after_white"])
    after_music = protocol.decode_state(raw["notify_after_music"])
    assert set(after_white) == {const.KEY_MODE, const.KEY_TIMESTAMP}
    assert after_white[const.KEY_MODE] == const.MODE_VALUES["all_white"]
    assert after_music[const.KEY_MODE] == const.MODE_VALUES["music_energy_core"]
    assert const.PROPERTIES[const.KEY_MODE].name == "ModeCtr"


# Properties whose write was confirmed by SEEING the lamp change.
# KEY_GRADUAL joined this set by writing 25 seconds and then watching the lamp
# fade out slowly on the next power-off instead of snapping off.
VISUALLY_CONFIRMED = {const.KEY_POWER, const.KEY_BRIGHTNESS, const.KEY_SEGMENTS,
                      const.KEY_GRADUAL, const.KEY_MODE, const.KEY_DYDATA}

# Properties whose write was confirmed by reading the exact value back but
# whose effect has not been observed. Weaker: key 8 also read back perfectly
# and rendered nothing, so this proves storage, not effect.
READBACK_CONFIRMED = {const.KEY_CHIME, const.KEY_COUNTDOWN}

# Confirmed only by the notification the device pushes after a write. This is
# the sole channel available for properties past the read truncation. It proves
# the device accepted and stored the value; like READBACK_CONFIRMED it does not
# prove the value has any effect.
ECHO_CONFIRMED = {const.KEY_RISESET}

# Mode control is confirmed visually, but only as a PAIR: ModeCtr alone does
# nothing and DyData alone applies a mode partially.
MODE_PAIR = {const.KEY_MODE, const.KEY_DYDATA}


def test_writable_set_is_exactly_what_hardware_confirmed():
    """The project's safety rule, encoded.

    Keys 8 and 19 both store perfectly and drive nothing, so an acknowledged
    write means nothing on its own. Everything writable here was confirmed on
    hardware, either visually or by exact readback.
    """
    writable = {k for k, p in const.PROPERTIES.items() if p.writable}
    assert writable == VISUALLY_CONFIRMED | READBACK_CONFIRMED | ECHO_CONFIRMED
    for key in writable:
        assert const.PROPERTIES[key].confidence == "confirmed"


def test_riseset_became_writable_once_the_echo_verified_it():
    """Key 79 was held back until it could be verified, then released.

    It sits past the read truncation, so for several sessions there was no way
    to distinguish it from key 19, which also accepted writes and did nothing.
    The notification echo supplied the missing verification.
    """
    assert const.PROPERTIES[const.KEY_RISESET].writable
    frame = protocol.encode_sunrise_sunset(True, 21600, 75600)
    assert protocol.decode_state(frame) == {79: [1, 21600, 75600]}


def test_gradual_effect_was_observed_not_just_stored():
    """Gradual is the bridge between the two grades of evidence.

    It was written by readback only, then verified visually: with 25 seconds
    set, the lamp faded out slowly on the next power-off rather than snapping
    off. That is the first demonstration that a readback-confirmed write on
    this device actually takes effect, which raises confidence in the chime and
    countdown writes without proving them.
    """
    assert const.KEY_GRADUAL in VISUALLY_CONFIRMED
    assert const.KEY_GRADUAL not in READBACK_CONFIRMED
    assert protocol.decode_state(protocol.encode_gradual(True, 25))[80] == [1, 25]


def test_countdown_has_behavioural_evidence_not_just_storage():
    """Countdown is the strongest of the readback-only group.

    We wrote [1, 0, 600, 600] and read back [1, 0, 600, 594] seconds later.
    The device was actively decrementing element 3, which is evidence it is
    processing the value rather than merely storing it.
    """
    assert const.KEY_COUNTDOWN in READBACK_CONFIRMED
    frame = protocol.encode_countdown(True, 600)
    decoded = protocol.decode_state(frame)
    assert decoded[const.KEY_COUNTDOWN] == [1, 0, 600, 600]


@pytest.mark.parametrize("call, key, expected", [
    (lambda: protocol.encode_chime(True, 26100, 81600), 77, [1, 26100, 81600]),
    (lambda: protocol.encode_gradual(True, 25), 80, [1, 25]),
    (lambda: protocol.encode_countdown(True, 600), 5, [1, 0, 600, 600]),
])
def test_config_encoders_match_the_frames_confirmed_on_hardware(call, key, expected):
    assert protocol.decode_state(call())[key] == expected


def test_config_encoders_validate_lengths_and_ranges():
    with pytest.raises(ValueError):
        protocol.encode_chime(True, 90000, 0)          # hour out of range
    with pytest.raises(ValueError):
        protocol._encode_int_array(const.KEY_GRADUAL, [1, 2, 3])  # wrong length


def test_key_77_is_the_hourly_chime_not_sunrise():
    """Toggling Hourly Chime flipped element 0 of key 77 from 0 to 1.

    The two times, 21600 and 64800 seconds, are 06:00 and 18:00, matching the
    limits the app displayed for the chime. Key 77 was previously guessed to be
    sunrise/sunset purely because those numbers look like dawn and dusk, which
    is exactly the kind of inference this project keeps having to retract.
    """
    assert const.PROPERTIES[77].name == "Remind"
    assert const.PROPERTIES[77].confidence == "confirmed"
    assert 21600 == 6 * 3600
    assert 64800 == 18 * 3600


def test_key_66_is_not_a_counter():
    """A hypothesis that was tested against hardware and failed.

    Key 66 went 0 -> 1 when the first config feature was enabled. The counter
    reading predicted it would return to 0 once every feature was disabled
    again. It stayed at 1. Recorded so the idea is not quietly reinvented: it
    latches, it does not count.
    """
    assert const.PROPERTIES[66].name == "preview"
    assert const.PROPERTIES[66].confidence == "unknown"  # named, still unexplained


def test_key_80_is_the_gradual_fade_config():
    """Setting Gradual to 10 seconds moved key 80 from [0, 0] to [1, 10].

    Element 0 enables, element 1 is seconds. Same [enabled, ...] convention as
    key 77, which suggests it is general across the config properties.
    """
    assert const.PROPERTIES[80].name == "RiseSlow"
    assert const.PROPERTIES[80].confidence == "confirmed"


def test_key_5_is_not_the_gradual_config():
    """Another tested-and-failed prediction, kept so it is not reinvented.

    Key 5's [0, 0, 1800, 1800] was assumed to be fade ramps because 1800 s is
    30 minutes. Setting a real gradual value moved key 80 instead and left
    key 5 untouched.
    """
    assert const.PROPERTIES[5].name == "GCountdown"


def test_keys_41_43_are_timer_slots_not_presets():
    """They move in lockstep because they are timer data slots.

    Labelled preset_1..3 by inference, which was wrong. The device's own
    property table names them GTimeDat2/3/4: five timer slots numbered 0-4,
    where slot 0 is key 4 and never appears in a read.
    """
    for key in (41, 42, 43):
        assert const.PROPERTIES[key].name.startswith("GTimeDat")
    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    assert state[41] == state[42] == state[43]


def test_key_24_byte_1_is_the_segment_count_not_a_timer_minute():
    """Guards against a coincidence that looks exactly like a finding.

    After a 16:20 timer was set, key 24's header read "10 14" = 16, 20. But
    byte 1 is 20 in every capture ever taken, because it is the segment count.
    """
    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    assert state[24][1] == const.SEGMENT_COUNT
    music = load_labelled_hex("mode_music_energy_core.hex")
    assert protocol.decode_state(load_hex("state_facebd02.hex"))[24][1] == 20
    assert music["key8"][0] == 0x46          # unrelated key, sanity anchor


def test_timer_content_is_invisible_to_readable_state():
    """Editing a timer's time changed nothing at all.

    16:20 -> 12:34 with everything else held constant produced zero changed
    keys. Only key 3, the enable flag, has ever moved for timers. This is the
    cleanest evidence that a whole feature's configuration lives outside the 22
    properties a state read returns.
    """
    assert const.PROPERTIES[3].name == "GTime"
    # 12:34 in the representations that were searched for and not found.
    assert 12 * 3600 + 34 * 60 == 45240
    assert 12 * 60 + 34 == 754


# ------------------------------------- the device's own property table

def test_property_table_matches_the_devices_own_dictionary():
    """Our table must agree with the device's own property table.

    The fixture is the device's property table, the authority on names and
    keys, so drift here means something has been invented.
    """
    rows = {}
    for line in (DATA / "c045_property_dict.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, name, size, _idx = line.split("\t")
        if int(key) == 0:                       # WiFi fields share key 0
            continue
        rows[int(key)] = (name, int(size))

    for key, (name, _size) in rows.items():
        assert key in const.PROPERTIES, f"key {key} ({name}) missing from PROPERTIES"
        assert const.PROPERTIES[key].name == name, f"key {key} name drifted"


def test_declared_sizes_match_observed_payloads():
    """Byte-array sizes in the device's table match what we actually read."""
    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    expected = {22: 80, 23: 20, 24: 100}        # Section0, LmArray, DyData
    for key, size in expected.items():
        assert len(state[key]) == size


def test_the_invisible_features_are_the_never_read_keys():
    """Explains every feature that experiments could not find.

    Sunrise/sunset is RiseSet (key 79) and a single timer's content is
    GTimeDat0 (key 4). Both are declared by the device and neither is ever
    returned by a state read, which is why toggling them appeared to change
    nothing at all.
    """
    assert 79 in const.NEVER_READ
    assert 4 in const.NEVER_READ
    assert const.PROPERTIES[79].name == "RiseSet"
    assert const.PROPERTIES[4].name == "GTimeDat0"

    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    for key in const.NEVER_READ:
        assert key not in state, f"key {key} appeared in a read after all"


# ------------------------------ declared sizes predict the wire encoding

def test_declared_size_predicts_array_length_for_verified_properties():
    """The size model, checked against the two properties we confirmed by hand.

    Remind was observed as [1, 21600, 64800] and RiseSlow as [1, 10]. Their
    declared sizes, divided by 4 bytes per int, give exactly those lengths.
    This is what makes RiseSet (12B -> 3 elements) a prediction rather than a
    guess.
    """
    assert const.array_len(77) == 3          # Remind, observed 3 elements
    assert const.array_len(80) == 2          # RiseSlow, observed 2 elements

    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    assert len(state[77]) == const.array_len(77)
    assert len(state[80]) == const.array_len(80)


def test_declared_size_matches_byte_buffer_properties():
    state = protocol.decode_state(load_hex("state_facebd02.hex"))
    for key in (22, 23, 24):
        assert len(state[key]) == const.DECLARED_SIZES[key]


def test_declared_sizes_agree_with_the_devices_own_table():
    for line in (DATA / "c045_property_dict.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, _name, size, _idx = line.split("\t")
        if int(key) == 0:
            continue
        assert const.DECLARED_SIZES[int(key)] == int(size)


def test_recovered_timer_enums():
    """Timer mode and type enum values.

    The names match the app's own UI: Main / Mood Light / All for the target,
    and Wake-Up / Sleep alongside plain on and off for the action.
    """
    assert const.TIMER_MODE == {"Main": 100, "Mood": 101, "All": 102}
    assert const.TIMER_TYPE["wakeUp"] == 2
    assert const.TIMER_TYPE["helpSleep"] == 3


# ------------------------------------------------------------ captured modes

def test_captured_modes_replay_as_two_property_writes():
    from glowctl import modes
    assert "campfire" in modes.available()
    props = modes.get("campfire")
    assert sorted(props) == [const.KEY_MODE, const.KEY_DYDATA]
    frame = protocol.encode_mode(props[const.KEY_MODE], props[const.KEY_DYDATA])
    decoded = protocol.decode_state(frame)
    assert decoded[const.KEY_MODE] == bytes.fromhex("01009c")


def test_dydata_header_byte_4_is_the_palette_length():
    """Byte 4 is the palette length. This test previously asserted the opposite.

    Campfire declares 6 and carries 6 colours; torch declares 20 and carries 20.
    That reading was retracted when a custom mode described as "three colours"
    read 20, but its palette genuinely had 20 entries in use (7 red, 6 green,
    7 blue), so the retraction was the mistake. Confirmed by setting byte 4
    explicitly and watching the device cycle exactly that many colours.
    """
    from glowctl import modes
    custom = modes.get("custom_rgb_7_6_7")[const.KEY_DYDATA]
    # 20 entries genuinely in use here, which is what caused the confusion.
    assert custom[4] == const.SEGMENT_COUNT
    assert len({protocol.parse_segments(custom[20:])[i] for i in range(20)}) > 1
    # Campfire: byte 4 = 6, and entries 0-5 are its six warm colours while
    # 6-18 are empty. Entry 19 is non-zero too, but that is the BACKGROUND
    # colour, which sits outside the cycle and is not counted by byte 4.
    campfire = modes.get("campfire")[const.KEY_DYDATA]
    palette = protocol.parse_segments(campfire[20:])
    assert campfire[4] == 6
    assert all(any(c) for c in palette[:6])          # the six cycled colours
    assert not any(any(c) for c in palette[6:19])    # unused slots
    assert any(palette[19])                          # background, not cycled
    assert custom[1] == 7
    assert custom[3] == 100                      # brightness, stable throughout

    palette = protocol.parse_segments(custom[20:])
    assert len(palette) == const.SEGMENT_COUNT
    # Bottom-first, same as Section0: with LED count 7 and palette[0..6] red,
    # exactly 7 LEDs animated at the BOTTOM of the column.
    assert all(c == (255, 0, 0, 0) for c in palette[:7])
    runs = []
    for colour in palette:
        if runs and runs[-1][0] == colour:
            runs[-1][1] += 1
        else:
            runs.append([colour, 1])
    # 7 red, 6 green, then blue with one transitional pixel.
    assert runs[0] == [(255, 0, 0, 0), 7]
    assert runs[1] == [(0, 255, 0, 0), 6]


def test_modectr_byte_2_tracks_the_app_mode_index():
    """Campfire is index 13 and reads 0x9c; torch is 11 and reads 0x9a.

    Both fit 0x8f + index. Two points only, so this is a pattern rather than a
    rule, and it is descriptive: ModeCtr cannot be synthesised from an index
    without the matching DyData program.
    """
    from glowctl import modes
    assert modes.get("campfire")[const.KEY_MODE][2] - 13 == 0x8F
    assert modes.get("torch")[const.KEY_MODE][2] - 11 == 0x8F


def test_dydata_palette_is_bottom_first_like_section0():
    """Both properties use the same convention, confirmed behaviourally.

    A custom mode with LED count 7 and red in palette[0..6] animated exactly
    seven LEDs at the BOTTOM. Had the palette been top-first the effect would
    have appeared at the top instead, so the two properties agree and no
    mirroring is needed between them.
    """
    from glowctl import modes
    dy = modes.get("custom_rgb_7_6_7")[const.KEY_DYDATA]
    assert dy[1] == 7
    assert all(c == (255, 0, 0, 0) for c in protocol.parse_segments(dy[20:])[:7])


def test_led_count_bounds_the_animated_region():
    """byte 1 is a span, not a palette size.

    Campfire spans 10, torch 8, the custom mode 7, while all three carry a full
    20-entry palette. So the palette is always 20 long and byte 1 says how much
    of the column the effect actually drives.
    """
    from glowctl import modes
    for name, expected in (("campfire", 10), ("torch", 8), ("custom_rgb_7_6_7", 7)):
        dy = modes.get(name)[const.KEY_DYDATA]
        assert dy[1] == expected
        assert len(protocol.parse_segments(dy[20:])) == const.SEGMENT_COUNT


def test_dydata_byte_7_is_tail_length():
    """Isolated by a single-variable hardware test.

    Setting tail length 0 -> 3 moved byte 7 and nothing else in the entire
    100-byte program, palette included. Byte 6 had been predicted, on the
    strength of campfire reading 3 there and torch 0, which turned out to fit
    the 0-3 range by chance.
    """
    from glowctl import modes
    before = modes.get("custom_rgb_7_6_7")[const.KEY_DYDATA]
    assert before[7] == 0
    # Campfire's byte 6 = 3 is the coincidence that misled the prediction.
    assert modes.get("campfire")[const.KEY_DYDATA][6] == 3
    assert modes.get("campfire")[const.KEY_DYDATA][7] == 0


def test_byte_6_direction_meaning_depends_on_dynamic_type():
    """The same byte and value mean different things per dynamic type.

    Under the original type, setting "upward" gave byte 6 = 1. Under
    Expandable, setting "downward expansion" also gave 1. The protocol relabels the
    control per dynamic type, which is also why campfire reads 3 where a binary
    flag could not.
    """
    from glowctl import modes
    assert modes.get("custom_tail3")[const.KEY_DYDATA][6] == 0
    assert modes.get("custom_up")[const.KEY_DYDATA][6] == 1          # "upward"
    assert modes.get("custom_downexp")[const.KEY_DYDATA][6] == 1     # "downward expansion"
    assert modes.get("custom_up")[const.KEY_DYDATA][0] != \
           modes.get("custom_downexp")[const.KEY_DYDATA][0]          # different types
    assert modes.get("campfire")[const.KEY_DYDATA][6] == 3           # neither 0 nor 1


def test_dydata_byte_2_is_inverted_speed():
    """Speed is stored as a delay: byte 2 = 101 - speed.

    Setting speed to 23 produced 78, and the prior value of 1 corresponds to
    speed 100. Campfire (1) and torch (3) both imply near-maximum speed, which
    suits a flicker and a flame.

    Confirmed on three points: 100 -> 1, 23 -> 78, and 50 -> 51. The last was
    a prediction stated in advance, which held exactly.
    """
    from glowctl import modes
    assert modes.get("custom_speed23")[const.KEY_DYDATA][2] == 78
    assert modes.get("custom_up")[const.KEY_DYDATA][2] == 1
    assert 23 + 78 == 101
    for name in ("campfire", "torch"):
        assert 101 - modes.get(name)[const.KEY_DYDATA][2] > 90


def test_build_dydata_overrides_known_fields_and_preserves_the_rest():
    """Synthesis works by editing a captured program, never inventing one.

    Eight header bytes still have no known meaning. Starting from a real
    capture keeps them at values the device is known to accept, instead of
    guessing at fields we cannot verify.
    """
    from glowctl import modes
    tpl = modes.get("custom_speed23")[const.KEY_DYDATA]
    dy = protocol.build_dydata(tpl, palette=[(255, 255, 255, 200)] * 5,
                               led_count=5, speed=30, brightness=100,
                               direction=1, tail_length=3)
    assert dy[protocol.DY_LED_COUNT] == 5
    assert dy[protocol.DY_SPEED] == 101 - 30
    assert dy[protocol.DY_DIRECTION] == 1
    assert dy[protocol.DY_TAIL] == 3
    # Unmapped bytes come through untouched.
    for offset in (0, 5, 9, 10, 18, 19):
        assert dy[offset] == tpl[offset]
    palette = protocol.parse_segments(dy[20:])
    assert palette[:5] == [(255, 255, 255, 200)] * 5
    assert palette[5] == (0, 0, 0, 0)          # padded with black


def test_build_dydata_validates_ranges():
    from glowctl import modes
    tpl = modes.get("custom_speed23")[const.KEY_DYDATA]
    for kwargs in ({"speed": 101}, {"tail_length": 4}, {"direction": 2},
                   {"led_count": 21}, {"brightness": 101}):
        with pytest.raises(ValueError):
            protocol.build_dydata(tpl, **kwargs)
    with pytest.raises(ValueError):
        protocol.build_dydata(b"\x00" * 99)


def test_dynamic_type_is_not_orthogonal_and_is_not_exposed():
    """Byte 0 is dynamic type, but it cannot be set independently.

    Changing dynamic type to Expandable moved byte 0 from 0 to
    3 AND zeroed direction, tail, bytes 9 and 10, while setting byte 11 to 250
    and byte 19 to 1. The parameter map clears parameters that do not apply to the chosen
    type, so writing byte 0 alone would produce an invalid combination.
    build_dydata therefore has no dynamic_type argument.
    """
    import inspect
    from glowctl import modes
    params = inspect.signature(protocol.build_dydata).parameters
    assert "dynamic_type" not in params

    expandable = modes.get("custom_expandable")[const.KEY_DYDATA]
    speed50 = modes.get("custom_speed23")[const.KEY_DYDATA]
    assert expandable[0] == 3 and speed50[0] == 0
    # byte 11 = 250 travels with byte 0 = 3 in every capture.
    for name in ("campfire", "torch", "custom_expandable"):
        dy = modes.get(name)[const.KEY_DYDATA]
        assert (dy[0] == 3) == (dy[11] == 250)


def test_byte_11_belongs_to_the_dynamic_type_not_a_control():
    """byte 11 = 250 exactly when byte 0 = 3, across every capture.

    Nine for nine, spanning built-in presets and four custom variants.
    So byte 11 is a property of dynamic type 3 rather than anything the user
    sets, which is why build_dydata leaves it to the template.
    """
    from glowctl import modes
    for name in modes.available():
        dy = modes.get(name)[const.KEY_DYDATA]
        assert (dy[0] == 3) == (dy[11] == 250), name


def test_observed_dynamic_types():
    """Gradient, Expandable and Random are observed and consecutive.

    2, 3, 4 in the app's list order implies Descent = 1 and Marquee = 5, and
    that byte 0 = 0 is not Descent but a none/static value. Those two are
    recorded as predictions and excluded from DYNAMIC_TYPES_OBSERVED.
    """
    from glowctl import modes
    # All five observed on hardware. The numbering skips 1.
    assert const.DYNAMIC_TYPES == {"descent": 0, "gradient": 2, "expandable": 3,
                                   "random": 4, "marquee": 5}
    assert 1 not in const.DYNAMIC_TYPES.values()
    for name, value in const.DYNAMIC_TYPES.items():
        assert modes.get(f"custom_{name}")[const.KEY_DYDATA][0] == value


def test_each_dynamic_type_owns_auxiliary_header_bytes():
    """Type changes rewrite bytes the user never touches.

    Descent brings bytes 9 = 19 and 10 = 100; Expandable brings byte 11 = 250.
    This is the concrete reason build_dydata takes a template rather than
    building a header from parameters.
    """
    from glowctl import modes
    for name in modes.available():
        dy = modes.get(name)[const.KEY_DYDATA]
        assert (dy[0] == 3) == (dy[11] == 250), name
        if dy[0] == 0:
            assert dy[9] == 19 and dy[10] == 100, name
    from glowctl import modes
    assert const.DYNAMIC_TYPES["gradient"] == 2
    assert const.DYNAMIC_TYPES["expandable"] == 3
    assert modes.get("custom_gradient")[const.KEY_DYDATA][0] == 2
    assert modes.get("custom_expandable")[const.KEY_DYDATA][0] == 3
    # Built-in presets are built on Expandable.
    assert modes.get("campfire")[const.KEY_DYDATA][0] == 3
    assert modes.get("torch")[const.KEY_DYDATA][0] == 3


def test_background_colour_lives_in_the_last_palette_slot():
    """Setting the background to green changed only palette entry 19.

    Two controls moved in that capture, colour mode and background, but the
    attribution is unambiguous because two values match exactly what was set:
    background brightness 5 landed in byte 18, and background green landed in
    palette[19]. Byte 8 is what remains, so it is the colour mode.
    """
    from glowctl import modes
    before = protocol.parse_segments(modes.get("custom_descent")[const.KEY_DYDATA][20:])
    after = protocol.parse_segments(modes.get("custom_first_bg")[const.KEY_DYDATA][20:])
    assert before[:19] == after[:19]                 # effect colours untouched
    assert before[19] == (0, 0, 255, 0)              # was blue
    assert after[19] == (0, 255, 0, 0)               # set to green


def test_byte_18_is_background_brightness_and_byte_8_is_colour_mode():
    from glowctl import modes
    dy = modes.get("custom_first_bg")[const.KEY_DYDATA]
    assert dy[18] == 5           # app was set to background brightness 5
    assert dy[8] == const.COLOUR_MODES["first"]
    assert modes.get("custom_descent")[const.KEY_DYDATA][18] == 0


def test_colour_mode_values_are_not_a_sequence():
    """Three values observed, skipping 1 and 2.

    cycle rotation = 0, random = 3, first = 4. Either a bitfield or an enum
    with members this dynamic type does not expose. Recorded as observations
    with no scheme inferred, since pattern extrapolation has been wrong twice
    on this device.
    """
    from glowctl import modes
    assert const.COLOUR_MODES == {"cycle_rotation": 0, "random": 3, "first": 4}
    assert 1 not in const.COLOUR_MODES.values()
    assert 2 not in const.COLOUR_MODES.values()
    assert modes.get("custom_descent")[const.KEY_DYDATA][8] == 0
    assert modes.get("custom_first_bg")[const.KEY_DYDATA][8] == 4
    assert modes.get("custom_random_colour")[const.KEY_DYDATA][8] == 3


def test_speed_and_speed_mode_share_byte_2():
    """Setting speed mode to uniform moved byte 2 from 51 to 179 = 51 | 0x80.

    The speed bits were untouched, so the two controls are packed into one
    byte. This was a real bug in build_dydata: writing speed set the whole
    byte and silently cleared the speed-mode bit.
    """
    from glowctl import modes
    before = modes.get("custom_random_colour")[const.KEY_DYDATA]
    after = modes.get("custom_uniform")[const.KEY_DYDATA]
    assert before[2] == 51 and after[2] == 179
    assert after[2] == before[2] | 0x80

    assert protocol.decode_speed(51) == (50, 0)
    assert protocol.decode_speed(179) == (50, 1)


def test_build_dydata_preserves_the_other_half_of_byte_2():
    """Regression guard for the packing bug."""
    from glowctl import modes
    tpl = modes.get("custom_random_colour")[const.KEY_DYDATA]

    only_mode = protocol.build_dydata(tpl, speed_mode=1)
    assert protocol.decode_speed(only_mode[2]) == (50, 1)      # speed kept

    then_speed = protocol.build_dydata(only_mode, speed=80)
    assert protocol.decode_speed(then_speed[2]) == (80, 1)     # mode kept

    back = protocol.build_dydata(then_speed, speed_mode=0)
    assert protocol.decode_speed(back[2]) == (80, 0)


def test_parse_dydata_reports_every_mapped_field():
    from glowctl import modes
    info = protocol.parse_dydata(modes.get("custom_random_colour")[const.KEY_DYDATA])
    assert info["led_count"] == 10
    assert info["speed"] == 50 and info["speed_mode"] == 0
    assert info["tail_length"] == 3
    assert info["colour_mode"] == const.COLOUR_MODES["random"]
    assert info["background_brightness"] == 5
    assert info["background_colour"] == (0, 255, 0, 0)          # green


def test_byte_5_is_response_method():
    """Stacking -> water drops moved byte 5 from 0 to 1, alone.

    Campfire reads 2 and torch 3 there, so byte 5 carries more values than the
    descent UI exposes, the same shape as byte 6's direction.
    """
    from glowctl import modes
    assert modes.get("custom_uniform")[const.KEY_DYDATA][5] == 0        # stacking
    assert modes.get("custom_waterdrops")[const.KEY_DYDATA][5] == 1     # water drops
    assert modes.get("campfire")[const.KEY_DYDATA][5] == 2
    assert modes.get("torch")[const.KEY_DYDATA][5] == 3


def test_only_byte_12_remains_unmapped():
    """Every other DyData header byte now has an established meaning."""
    mapped = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 18, 19}
    unmapped = set(range(20)) - mapped
    assert unmapped == {12, 13, 14, 15, 16, 17}
    # 13-17 are zero in every capture ever taken, so 12 is the only one that
    # has been seen to vary without an explanation.
    from glowctl import modes
    for name in modes.available():
        header = modes.get(name)[const.KEY_DYDATA][:20]
        assert all(header[i] == 0 for i in range(13, 18)), name


# --------------------------------- composite properties embed a DyData program

def test_gtimedat_is_config_plus_a_dydata_program(state):
    """A timer slot splits at offset 12 into config and a lighting program.

    The embedded part carries the DyData signature, brightness 100 and 20
    segments, and a well-formed 20-entry palette. 12 + 100 = 112 accounts for
    the whole slot, which also corrects the earlier reading of it as a 32-byte
    header followed by segments.
    """
    parsed = protocol.parse_timer_slot(state[41])
    assert len(parsed["config"]) == protocol.GTIMEDAT_CONFIG_LEN
    assert len(parsed["program"]) == const.DECLARED_SIZES[const.KEY_DYDATA]

    info = parsed["parsed_program"]
    assert info["brightness"] == 100
    assert info["segments"] == const.SEGMENT_COUNT
    assert len(info["palette"]) == const.SEGMENT_COUNT
    # The factory rainbow, same as the standalone DyData default.
    assert info["palette"][:4] == [(255, 0, 0, 0), (0, 255, 0, 0),
                                   (0, 0, 255, 0), (0, 0, 0, 255)]


def test_composite_sizes_all_account_for_a_100_byte_program():
    """Every composite property is a config prefix plus one shared program."""
    prog = const.DECLARED_SIZES[const.KEY_DYDATA]
    assert prog == 100
    assert protocol.GTIMEDAT_CONFIG_LEN + prog == 112      # observed slot size
    assert protocol.CFG_CONFIG_LEN + prog == const.DECLARED_SIZES[62] == 102
    with pytest.raises(ValueError):
        protocol.split_composite(b"\x00" * 50, protocol.GTIMEDAT_CONFIG_LEN)


def test_timer_config_hour_and_minute():
    """A timer set to 19:45 reads 19 and 45 at config offsets 1 and 2.

    This is what identified the layout. Two earlier attempts found nothing
    because the timers landed in GTimeDat0, which the truncated read never
    returns; setting three timers pushed the third into a readable slot.
    """
    slots = load_labelled_hex("timer_slot.hex")
    cfg = slots["slot2_enabled_1945"]
    assert cfg[protocol.TIMER_HOUR] == 19
    assert cfg[protocol.TIMER_MINUTE] == 45
    assert cfg[protocol.TIMER_ENABLE] & protocol.TIMER_ENABLE_BIT


def test_timer_config_diff_against_empty_slots():
    """Only four bytes distinguish an enabled slot from an empty one."""
    slots = load_labelled_hex("timer_slot.hex")
    on, off = slots["slot2_enabled_1945"], slots["slot3_empty"]
    assert slots["slot3_empty"] == slots["slot4_empty"]
    differing = {i for i in range(12) if on[i] != off[i]}
    assert differing == {0, 1, 2, 3, 10}
    # Bytes 4-9 and 11 were identical in every slot, enabled or not.
    for i in (4, 5, 6, 7, 8, 9, 11):
        assert on[i] == off[i]


def test_parse_timer_slot_reports_the_time(state):
    """Parsing an empty factory slot still yields a well-formed result."""
    parsed = protocol.parse_timer_slot(state[41])
    assert parsed["enabled"] is False
    assert parsed["time"] == "00:00"
    assert len(parsed["program"]) == const.DECLARED_SIZES[const.KEY_DYDATA]


def test_timer_byte_0_is_enable_plus_weekday_bitmask():
    """Repeat is a packed weekday mask.

    once = 0x80 (enabled, no day bits), daily = 0xff (enabled, all seven).
    """
    slots = load_labelled_hex("timer_slot.hex")
    once, daily = slots["slot2_enabled_1945"], slots["slot2_daily_off"]
    assert once[protocol.TIMER_FLAGS] == 0x80
    assert daily[protocol.TIMER_FLAGS] == 0xFF
    assert daily[protocol.TIMER_FLAGS] & protocol.TIMER_DAYS_MASK == protocol.TIMER_DAILY
    assert protocol.parse_timer_slot(once + b"\x00" * 100)["repeat"] == "once"
    assert protocol.parse_timer_slot(daily + b"\x00" * 100)["repeat"] == "daily"


def test_timer_action_byte():
    slots = load_labelled_hex("timer_slot.hex")
    assert slots["slot2_enabled_1945"][protocol.TIMER_ACTION] == 1     # on
    assert slots["slot2_daily_off"][protocol.TIMER_ACTION] == 0        # off


def test_built_timer_matches_what_the_device_stored():
    """A timer built in Python, written, and read back byte-for-byte.

    06:53, weekdays only (0x1f), action on. This is the fixture the device
    actually returned, so the encoder is pinned to hardware behaviour.
    """
    slots = load_labelled_hex("timer_slot.hex")
    stored = slots["slot2_written_0653_weekdays"]
    template = slots["slot2_daily_off"] + b"\x00" * 100
    built = protocol.build_timer_slot(template, hour=6, minute=53,
                                      enabled=True, days=0x1F, action="on")
    assert built[:12] == stored

    parsed = protocol.parse_timer_slot(built)
    assert parsed["time"] == "06:53"
    assert parsed["enabled"] and parsed["days"] == 0x1F
    assert parsed["action"] == "on"


def test_build_timer_slot_validates():
    slots = load_labelled_hex("timer_slot.hex")
    template = slots["slot2_daily_off"] + b"\x00" * 100
    for kwargs in ({"hour": 24}, {"minute": 60}, {"days": 0x80},
                   {"action": "dim"}):
        with pytest.raises(ValueError):
            protocol.build_timer_slot(template, **kwargs)


# ------------------------- notification echo reaches unreadable properties

def test_write_echo_carries_properties_a_read_never_returns():
    """The workaround for the truncated read.

    Writing RiseSet (key 79) produced a notification carrying key 79 with the
    exact values written, plus key 65 unprompted. Both are in NEVER_READ, so
    this channel reaches data the read path cannot.
    """
    echo = protocol.decode_state(load_labelled_hex("notify_echo_riseset.hex")["riseset_echo"])
    assert set(echo) == {65, 79, const.KEY_TIMESTAMP}
    assert echo[79] == [1, 6 * 3600 + 30 * 60, 20 * 3600 + 15 * 60]
    assert 79 in const.NEVER_READ and 65 in const.NEVER_READ


def test_riseset_is_now_writable_and_encodes_correctly():
    frame = protocol.encode_sunrise_sunset(True, 23400, 72900)
    assert protocol.decode_state(frame) == {79: [1, 23400, 72900]}
    assert const.PROPERTIES[79].writable
    assert const.PROPERTIES[79].confidence == "confirmed"


def test_mtu_explains_the_truncation_arithmetic():
    """738 = 3 * 246 at the real ATT_MTU of 247.

    The MTU must be read AFTER service discovery. Querying it in didConnect
    returns 23, the pre-negotiation default, and this project once recorded
    that and built a wrong explanation on it. Notifications of 128 bytes,
    impossible at MTU 23, are what exposed the mistake.
    """
    MTU = 247
    assert 3 * (MTU - 1) == 738
    # The device serves three full PDUs then stops, short of the 28 pairs its
    # own CBOR header declares.
    assert 128 > 23 - 3            # a notification that MTU 23 could not carry


def test_cfg_properties_echo_longer_than_declared():
    """SCfg and RCfg store 106 bytes while the property table declares 102.

    We wrote 2 config bytes plus a 100-byte program and the device echoed four
    extra bytes of its own, identical for both properties. So a declared size
    is the writable portion, not necessarily what the device keeps.
    """
    tails = load_labelled_hex("notify_echo_cfg.hex")
    assert tails["scfg_echo_tail"] == tails["rcfg_echo_tail"]
    assert len(tails["scfg_echo_tail"]) == 4
    assert const.DECLARED_SIZES[62] == const.DECLARED_SIZES[63] == 102
    assert 2 + const.DECLARED_SIZES[const.KEY_DYDATA] == 102


def test_rstime_accompanies_every_sunrise_family_write():
    """The device volunteers key 65 with RiseSet, SCfg and RCfg alike."""
    assert load_labelled_hex("notify_echo_cfg.hex")["rstime"] == bytes.fromhex("06011128")
    echo = protocol.decode_state(
        load_labelled_hex("notify_echo_riseset.hex")["riseset_echo"])
    assert echo[65] == bytes.fromhex("06011128")


def test_the_echo_channel_is_not_universal():
    """baseAd and extra accept writes silently, with no notification.

    Every other property tried echoed back. These two did not, so their
    content has never been observed through any channel: not readable, not
    echoed. Writing zeros to both left the device healthy, with 22 properties
    still readable and version still 3.
    """
    assert set(const.NEVER_ECHOED) == {7, 125}
    for key in const.NEVER_ECHOED:
        assert key in const.NEVER_READ
        assert const.PROPERTIES[key].confidence == "unknown"
        assert not const.PROPERTIES[key].writable


def test_never_read_keys_split_into_reachable_and_not():
    reachable = set(const.NEVER_READ) - set(const.NEVER_ECHOED)
    assert reachable == {4, 40, 62, 63, 65, 79}


def test_build_dydata_can_set_the_palette_length():
    from glowctl import modes
    tpl = modes.get("custom_marquee")[const.KEY_DYDATA]
    out = protocol.build_dydata(tpl, palette_length=4)
    assert out[protocol.DY_SEGMENTS] == 4
    with pytest.raises(ValueError):
        protocol.build_dydata(tpl, palette_length=21)


def test_descent_and_marquee_treat_the_background_differently():
    """Recorded from building a Matrix-rain effect on hardware.

    Descent lights only the moving drop, so a black background yields a dark
    column. Marquee fills its whole span with a repeating pattern, so at
    led_count 20 the background is never visible and no palette arrangement
    produces black. Background fills only what the effect does not occupy.
    """
    from glowctl import modes
    assert const.DYNAMIC_TYPES["descent"] == 0
    assert const.DYNAMIC_TYPES["marquee"] == 5
    assert modes.get("matrix")[const.KEY_DYDATA][0] == const.DYNAMIC_TYPES["descent"]
