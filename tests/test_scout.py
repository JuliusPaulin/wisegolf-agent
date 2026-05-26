"""Comprehensive tests for scout.py."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from wisegolf.models import TeeSlot
from wisegolf import scout as wg_scout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot(
    hhmm: str,
    quantity: int = 1,
    is_user_reservation: bool = False,
    is_sellable: bool = False,
    label: str | None = "res_golf",
    reservation_time_id: int = 1,
) -> TeeSlot:
    d = datetime(2026, 6, 7, int(hhmm[:2]), int(hhmm[3:]), tzinfo=timezone.utc)
    return TeeSlot(
        reservationTimeId=reservation_time_id,
        start=d, end=d, status=2, quantity=quantity,
        isUserReservation=is_user_reservation,
        isSellable=is_sellable, label=label,
    )


def _booked(hhmm: str, count: int, rid_base: int = 1, label: str = "res_golf") -> list[TeeSlot]:
    """N booked rows at the same time (API representation of N booked players)."""
    return [_slot(hhmm, quantity=1, reservation_time_id=rid_base + i, label=label) for i in range(count)]


def _two_spots(hhmm: str = "09:00", rid_base: int = 100) -> list[TeeSlot]:
    """2 booked rows → 2 open spots remaining (capacity 4 - 2 booked)."""
    return _booked(hhmm, 2, rid_base=rid_base)


TARGET = date(2026, 6, 7)


# ---------------------------------------------------------------------------
# _bookable
# ---------------------------------------------------------------------------

class TestBookable:
    """API rows = BOOKED player slots. open = 4 (capacity) - booked_count."""

    def test_1_booked_party2_included(self):
        result = wg_scout._bookable(_booked("09:00", 1), "08:00", "11:00", 2)
        assert len(result) == 1
        assert result[0][1] == 3  # open_count = 4 - 1

    def test_2_booked_party2_included(self):
        result = wg_scout._bookable(_booked("09:00", 2), "08:00", "11:00", 2)
        assert len(result) == 1
        assert result[0][1] == 2  # open_count = 4 - 2

    def test_3_booked_party2_excluded(self):
        assert wg_scout._bookable(_booked("09:00", 3), "08:00", "11:00", 2) == []

    def test_4_booked_fully_taken(self):
        assert wg_scout._bookable(_booked("09:00", 4), "08:00", "11:00", 1) == []

    def test_3_booked_party1_included(self):
        result = wg_scout._bookable(_booked("09:00", 3), "08:00", "11:00", 1)
        assert len(result) == 1
        assert result[0][1] == 1

    def test_returns_first_row_of_group(self):
        result = wg_scout._bookable(_booked("09:00", 2, rid_base=10), "08:00", "11:00", 2)
        slot, _ = result[0]
        assert slot.reservation_time_id == 10

    def test_before_window_excluded(self):
        assert wg_scout._bookable(_booked("07:30", 1), "08:00", "11:00", 1) == []

    def test_after_window_excluded(self):
        assert wg_scout._bookable(_booked("11:30", 1), "08:00", "11:00", 1) == []

    def test_window_boundary_inclusive(self):
        assert len(wg_scout._bookable(_booked("08:00", 1), "08:00", "11:00", 1)) == 1
        assert len(wg_scout._bookable(_booked("11:00", 1), "08:00", "11:00", 1)) == 1

    def test_pakkasvaraus_excluded(self):
        assert wg_scout._bookable(_booked("09:00", 1, label="Pakkasvaraus"), "08:00", "11:00", 1) == []

    def test_pakkasvaraus_mixed_excluded(self):
        slots = _booked("09:00", 1, label="Pakkasvaraus") + _booked("09:00", 1, rid_base=10)
        assert wg_scout._bookable(slots, "08:00", "11:00", 1) == []

    def test_multiple_times_sorted(self):
        slots = (
            _booked("07:00", 1, rid_base=1) +
            _booked("09:00", 1, rid_base=3) +
            _booked("10:00", 2, rid_base=5) +
            _booked("12:00", 1, rid_base=7)
        )
        result = wg_scout._bookable(slots, "08:00", "11:00", 2)
        assert [s.reservation_time_id for s, _ in result] == [3, 5]

    def test_real_world_07_50(self):
        result = wg_scout._bookable(_booked("07:50", 1), "07:00", "11:00", 2)
        assert len(result) == 1 and result[0][1] == 3

    def test_real_world_08_00(self):
        assert wg_scout._bookable(_booked("08:00", 3), "07:00", "11:00", 2) == []

    def test_real_world_09_10(self):
        assert len(wg_scout._bookable(_booked("09:10", 3), "07:00", "11:00", 1)) == 1
        assert wg_scout._bookable(_booked("09:10", 3), "07:00", "11:00", 2) == []

    def test_empty_slots(self):
        assert wg_scout._bookable([], "08:00", "11:00", 2) == []


# ---------------------------------------------------------------------------
# _push_pushover
# ---------------------------------------------------------------------------

class TestPushPushover:
    def test_missing_token_skips(self):
        with patch.dict(os.environ, {"WISEGOLF_PUSHOVER_TOKEN": "", "WISEGOLF_PUSHOVER_USER": "u"}):
            with patch("wisegolf.scout.httpx.post") as m:
                wg_scout._push_pushover("msg", "title")
                m.assert_not_called()

    def test_missing_user_skips(self):
        with patch.dict(os.environ, {"WISEGOLF_PUSHOVER_TOKEN": "t", "WISEGOLF_PUSHOVER_USER": ""}):
            with patch("wisegolf.scout.httpx.post") as m:
                wg_scout._push_pushover("msg", "title")
                m.assert_not_called()

    @respx.mock
    def test_sends_correct_payload(self):
        with patch.dict(os.environ, {"WISEGOLF_PUSHOVER_TOKEN": "tok", "WISEGOLF_PUSHOVER_USER": "usr"}):
            route = respx.post("https://api.pushover.net/1/messages.json").mock(return_value=httpx.Response(200))
            wg_scout._push_pushover("slot found", "title")
            assert route.called

    def test_network_error_swallowed(self):
        with patch.dict(os.environ, {"WISEGOLF_PUSHOVER_TOKEN": "t", "WISEGOLF_PUSHOVER_USER": "u"}):
            with patch("wisegolf.scout.httpx.post", side_effect=Exception("down")):
                wg_scout._push_pushover("msg", "title")  # must not raise


# ---------------------------------------------------------------------------
# _ntfy_send_with_actions
# ---------------------------------------------------------------------------

class TestNtfySendWithActions:
    @respx.mock
    def test_sends_to_correct_url(self):
        route = respx.post("https://ntfy.sh/my-topic").mock(return_value=httpx.Response(200))
        since = wg_scout._ntfy_send_with_actions("my-topic", "msg", "title")
        assert route.called
        req = route.calls[0].request
        assert req.content == b"msg"
        assert req.headers["Title"] == "title"
        assert "Book" in req.headers["Actions"]
        assert "Skip" in req.headers["Actions"]
        assert "my-topic-response" in req.headers["Actions"]
        assert isinstance(since, int)

    @respx.mock
    def test_returns_timestamp_before_send(self):
        import time
        respx.post("https://ntfy.sh/t").mock(return_value=httpx.Response(200))
        before = int(time.time())
        since = wg_scout._ntfy_send_with_actions("t", "msg", "title")
        assert since >= before


# ---------------------------------------------------------------------------
# _ntfy_poll_response
# ---------------------------------------------------------------------------

class TestNtfyPollResponse:
    def _make_event(self, body: str) -> str:
        return json.dumps({"event": "message", "message": body})

    @respx.mock
    def test_book_response_returns_book(self):
        respx.get("https://ntfy.sh/my-topic-response/json").mock(
            return_value=httpx.Response(200, text=self._make_event("book"))
        )
        assert wg_scout._ntfy_poll_response("my-topic", since=0, timeout_s=10) == "book"

    @respx.mock
    def test_skip_response_returns_skip(self):
        respx.get("https://ntfy.sh/my-topic-response/json").mock(
            return_value=httpx.Response(200, text=self._make_event("skip"))
        )
        assert wg_scout._ntfy_poll_response("my-topic", since=0, timeout_s=10) == "skip"

    @respx.mock
    def test_stop_response_returns_stop(self):
        respx.get("https://ntfy.sh/my-topic-response/json").mock(
            return_value=httpx.Response(200, text=self._make_event("stop"))
        )
        assert wg_scout._ntfy_poll_response("my-topic", since=0, timeout_s=10) == "stop"

    @respx.mock
    def test_timeout_returns_none(self):
        respx.get("https://ntfy.sh/my-topic-response/json").mock(
            return_value=httpx.Response(200, text="")
        )
        with patch("wisegolf.scout.time.sleep"):
            result = wg_scout._ntfy_poll_response("my-topic", since=0, timeout_s=0)
        assert result is None

    @respx.mock
    def test_ignores_non_message_events(self):
        events = "\n".join([
            json.dumps({"event": "keepalive"}),
            json.dumps({"event": "message", "message": "book"}),
        ])
        respx.get("https://ntfy.sh/t-response/json").mock(
            return_value=httpx.Response(200, text=events)
        )
        assert wg_scout._ntfy_poll_response("t", since=0, timeout_s=10) == "book"

    @respx.mock
    def test_network_error_continues(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ConnectError("down")
            return httpx.Response(200, text=json.dumps({"event": "message", "message": "skip"}))

        respx.get("https://ntfy.sh/t-response/json").mock(side_effect=handler)
        with patch("wisegolf.scout.time.sleep"):
            result = wg_scout._ntfy_poll_response("t", since=0, timeout_s=30)
        assert result == "skip"


# ---------------------------------------------------------------------------
# ask_to_book
# ---------------------------------------------------------------------------

class TestAskToBook:
    def test_ntfy_book_returns_true(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": "test-topic"}), \
             patch("wisegolf.scout._ntfy_send_with_actions", return_value=1000), \
             patch("wisegolf.scout._ntfy_poll_response", return_value="book"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is True

    def test_ntfy_skip_returns_false(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": "test-topic"}), \
             patch("wisegolf.scout._ntfy_send_with_actions", return_value=1000), \
             patch("wisegolf.scout._ntfy_poll_response", return_value="skip"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is False

    def test_ntfy_stop_returns_none(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": "test-topic"}), \
             patch("wisegolf.scout._ntfy_send_with_actions", return_value=1000), \
             patch("wisegolf.scout._ntfy_poll_response", return_value="stop"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is None

    def test_ntfy_timeout_returns_none(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": "test-topic"}), \
             patch("wisegolf.scout._ntfy_send_with_actions", return_value=1000), \
             patch("wisegolf.scout._ntfy_poll_response", return_value=None):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is None

    def test_ntfy_failure_falls_back_to_terminal(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": "test-topic"}), \
             patch("wisegolf.scout._ntfy_send_with_actions", side_effect=Exception("down")), \
             patch("builtins.input", return_value="y"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is True

    def test_no_ntfy_uses_terminal_y(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": ""}), \
             patch("builtins.input", return_value="y"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is True

    def test_no_ntfy_uses_terminal_n(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": ""}), \
             patch("builtins.input", return_value="n"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is False

    def test_no_ntfy_q_returns_none(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": ""}), \
             patch("builtins.input", return_value="q"):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is None

    def test_no_ntfy_eof_returns_none(self):
        slot = _slot("09:00")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": ""}), \
             patch("builtins.input", side_effect=EOFError):
            assert wg_scout.ask_to_book(slot, 2, TARGET) is None

    def test_message_printed_to_terminal(self, capsys):
        slot = _slot("09:30")
        with patch.dict(os.environ, {"WISEGOLF_NTFY_TOPIC": ""}), \
             patch("builtins.input", return_value="n"):
            wg_scout.ask_to_book(slot, 3, TARGET)
        out = capsys.readouterr().out
        assert "09:30" in out
        assert "07.06.2026" in out
        assert "3" in out


# ---------------------------------------------------------------------------
# watch() — loop behaviour
# ---------------------------------------------------------------------------

def _make_cfg():
    from zoneinfo import ZoneInfo
    cfg = MagicMock()
    cfg.snipe_tz = ZoneInfo("Europe/Helsinki")
    cfg.course_id = 28
    return cfg


@contextmanager
def _mock_client(slots_by_date: dict):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.list_slots.side_effect = lambda d, *a, **kw: slots_by_date.get(d, [])
    with patch("wisegolf.scout.WiseGolfClient", return_value=mock_client):
        yield mock_client


class TestWatch:
    def test_no_slots_polls_then_finds(self):
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            return spots if call_count["n"] > 1 else []

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125], poll_s=1)

        assert call_count["n"] == 2

    def test_slot_found_ask_called(self):
        spots = _two_spots()
        with _mock_client({TARGET: spots}), \
             patch("wisegolf.scout.ask_to_book", return_value=True) as mock_ask, \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])
        mock_ask.assert_called_once_with(spots[0], 2, TARGET)

    def test_user_skips_keeps_watching(self):
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            return spots

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots
        ask_responses = iter([False, False, True])

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", side_effect=ask_responses), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

        assert call_count["n"] == 3

    def test_booking_failure_keeps_watching(self):
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            return spots

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", side_effect=iter([False, True])), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

        assert call_count["n"] == 2

    def test_multiple_dates_all_booked(self):
        d1, d2 = date(2026, 6, 7), date(2026, 6, 14)
        with _mock_client({d1: _two_spots(), d2: _two_spots()}), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[d1, d2],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

    def test_api_error_continues(self):
        from wisegolf.client import WiseGolfError
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise WiseGolfError("401", status=401)
            return spots

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

        assert call_count["n"] == 2

    def test_generic_exception_continues(self):
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("flap")
            return spots

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

        assert call_count["n"] == 2

    def test_sleep_called_with_poll_interval(self):
        spots = _two_spots()
        call_count = {"n": 0}

        def list_slots(d, *a, **kw):
            call_count["n"] += 1
            return spots if call_count["n"] > 1 else []

        mc = MagicMock()
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        mc.list_slots.side_effect = list_slots

        with patch("wisegolf.scout.WiseGolfClient", return_value=mc), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout._book_now", return_value=True), \
             patch("wisegolf.scout.time.sleep") as mock_sleep:
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125], poll_s=42)

        mock_sleep.assert_called_once_with(42)

    def test_user_stop_removes_from_watching(self):
        spots = _two_spots()
        with _mock_client({TARGET: spots}), \
             patch("wisegolf.scout.ask_to_book", return_value=None), \
             patch("wisegolf.scout._book_now") as mock_book, \
             patch("wisegolf.scout.time.sleep"):
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])
        mock_book.assert_not_called()  # stop = don't book, just remove

    def test_first_available_time_used(self):
        s1a = _slot("08:00", reservation_time_id=1)
        s1b = _slot("08:00", reservation_time_id=2)
        s2a = _slot("09:00", reservation_time_id=3)
        s2b = _slot("09:00", reservation_time_id=4)

        with _mock_client({TARGET: [s1a, s1b, s2a, s2b]}), \
             patch("wisegolf.scout.ask_to_book", return_value=True), \
             patch("wisegolf.scout.time.sleep"), \
             patch("wisegolf.scout._book_now", return_value=True) as mock_book:
            wg_scout.watch(cfg=_make_cfg(), targets=[TARGET],
                           window_start="08:00", window_end="11:00",
                           party=2, person_ids=[37105, 37125])

        booked_slot = mock_book.call_args[0][1]
        assert booked_slot.reservation_time_id == 1
