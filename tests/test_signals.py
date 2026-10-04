import numpy as np

from clipfactory.analyst import signals


def test_pick_peaks_finds_spikes_and_respects_separation():
    score = np.zeros(3600)
    score[1000] = 1.0
    score[1030] = 0.9  # too close to 1000, suppressed
    score[2500] = 0.8
    peaks = signals.pick_peaks(score, k=5, window=80)
    assert [p.t for p in peaks] == [1000, 2500]
    p = peaks[0]
    assert p.end - p.start == 80
    assert p.start < p.t < p.end
    assert p.t - p.start > p.end - p.t  # window leans before the payoff


def test_pick_peaks_skips_stream_start_and_mask():
    score = np.zeros(2000)
    score[100] = 1.0
    score[900] = 0.7
    score[1500] = 0.6
    mask = np.ones(2000, dtype=bool)
    mask[800:1000] = False
    peaks = signals.pick_peaks(score, k=5, window=60, skip_start=240, mask=mask)
    assert [p.t for p in peaks] == [1500]


def test_clips_signal_peaks_on_popular_clip():
    clips = [
        {"offset": 100, "duration": 30, "views": 50, "is_gta": True},
        {"offset": 1000, "duration": 30, "views": 20000, "is_gta": True},
        {"offset": 2000, "duration": 30, "views": 99999, "is_gta": False},  # other game: ignored
    ]
    s = signals.clips_signal(clips, 3000)
    assert 1000 <= int(np.argmax(s)) <= 1031
    assert s[2010] == 0


def test_audio_signal_flags_loud_moment():
    rng = np.random.default_rng(0)
    db = -30 + rng.normal(0, 1, 1800)
    db[900:905] = -8
    s = signals.audio_signal(db)
    assert 895 <= int(np.argmax(s)) <= 910


def test_chat_signal_shifts_for_delay():
    counts = np.ones(1200)
    counts[600:610] = 40
    s = signals.chat_signal(counts, delay=7)
    assert 585 <= int(np.argmax(s)) <= 605  # earlier than the raw burst (600-610)


def test_heatmap_and_fuse():
    heat = [{"start_time": i * 10, "end_time": (i + 1) * 10, "value": 0.2} for i in range(60)]
    heat[30]["value"] = 1.0
    h = signals.heatmap_signal(heat, 600)
    assert h[305] > 0.999 and h[50] == 0
    fused = signals.fuse({"heatmap": h, "audio": np.zeros(600), "chat": None}, {"heatmap": 0.35, "audio": 0.2})
    assert int(np.argmax(fused)) in range(300, 310)


def test_game_mask_labels_by_nearest_clip():
    clips = [{"offset": 100, "is_gta": True}, {"offset": 1000, "is_gta": False}]
    m = signals.game_mask(clips, 1200)
    assert m[150] and not m[900]
    assert signals.game_mask([{"offset": 5, "is_gta": True}], 100) is None
