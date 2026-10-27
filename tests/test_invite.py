from telecaster.invite import make_invite, parse_invite, relay_url_for_host


def test_round_trip():
    link = make_invite("ws://203.0.113.7:8765", "K7QX2M")
    assert link.startswith("telecaster://join?")
    assert parse_invite(link) == ("ws://203.0.113.7:8765", "K7QX2M")


def test_found_inside_a_chat_message():
    link = make_invite("wss://relay.example.com/tc", "ROOM_1")
    message = f"hey join my stream drawing thing: {link}. thanks!"
    assert parse_invite(message) == ("wss://relay.example.com/tc", "ROOM_1")


def test_rejects_bad_links():
    assert parse_invite("") is None
    assert parse_invite("https://example.com") is None
    assert parse_invite("telecaster://join?relay=http%3A%2F%2Fx&room=ABCDEF") is None
    assert parse_invite("telecaster://join?relay=ws%3A%2F%2Fx%3A1&room=a%20b") is None
    assert parse_invite("telecaster://other?relay=ws%3A%2F%2Fx%3A1&room=ABCDEF") is None


def test_ipv6_host_is_bracketed():
    assert relay_url_for_host("2001:db8::1", 8765) == "ws://[2001:db8::1]:8765"
    assert parse_invite(make_invite(relay_url_for_host("2001:db8::1", 8765), "ABCDEF"))
