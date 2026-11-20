"""Tag normalisation and the file-name fallback (docs/protocols.md §4 `queue`)."""

from pathlib import Path

from tidalviz.player.tags import MAX_TEXT, TrackTags, clean_text, tags_for


def test_clean_text_strips_whitespace_and_treats_empty_as_missing():
    assert clean_text("  Boards of Canada \n") == "Boards of Canada"
    assert clean_text("   ") == ""
    assert clean_text(None) == ""
    assert clean_text(42) == ""


def test_clean_text_drops_control_characters_and_keeps_non_ascii():
    assert clean_text("Sigur\x00 R\x07ós\x1b[31m") == "Sigur Rós[31m"
    assert clean_text("a\x00b\tc\nd") == "abcd"
    assert clean_text("Björk – Jóga") == "Björk – Jóga"


def test_clean_text_caps_the_length():
    assert len(clean_text("x" * 5000)) == MAX_TEXT
    assert clean_text("é" * 5000) == "é" * MAX_TEXT


def test_tags_for_uses_the_tags_when_present():
    t = tags_for(Path("/music/a/b.flac"), artist=" A ", album="B", title="T")
    assert t == TrackTags(artist="A", album="B", title="T")


def test_a_missing_title_falls_back_to_the_file_name_without_its_extension():
    t = tags_for(Path("/m/Artist/Album/03 - Song Name.mp3"), artist="", album="", title=None)
    assert t.title == "03 - Song Name"
    assert t.artist == "" and t.album == ""  # never guessed from folders or the file name


def test_the_fallback_never_contains_a_directory():
    t = tags_for(Path("/home/secret/Music/x.ogg"), artist=None, album=None, title="  ")
    assert t.title == "x"
    assert "secret" not in repr(t)


def test_a_dotted_name_keeps_everything_before_the_last_extension():
    assert tags_for(Path("a.b.c.m4a"), artist=None, album=None, title=None).title == "a.b.c"


def test_clean_text_drops_hiding_characters_but_keeps_joiners_and_new_characters():
    assert clean_text("Evil\u202eterces") == "Evilterces"  # U+202E (bidi override) gone
    assert clean_text("a\u2028b\u2029c") == "abc"  # Zl, Zp
    assert clean_text("x\ue000y\ud800z") == "xyz"  # Co (private use), Cs (lone surrogate)
    assert clean_text("a\u200bb\u2066c\U000e0041d") == "abcd"  # zero-width space, isolate, tag char
    assert clean_text("ab\u200cc\u200dd") == "ab\u200cc\u200dd"  # joiners that real text needs
    assert clean_text("new\u0378char") == "new\u0378char"  # Cn: newer than Python's tables, kept
    assert clean_text("日本語 タイトル") == "日本語 タイトル"
