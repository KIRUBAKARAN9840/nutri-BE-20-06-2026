from datetime import date, time

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.sessions import _domain as d


def _vibes(*v):
    return d.WorkoutVibes(tuple(v))


class TestWorkoutVibes:
    def test_accepts_known(self):
        v = _vibes("HIIT", "Cardio")
        assert v.as_list() == ["HIIT", "Cardio"]

    def test_dedupes(self):
        v = _vibes("HIIT", "HIIT", "Cardio")
        assert v.as_list() == ["HIIT", "Cardio"]

    def test_empty_rejected(self):
        with pytest.raises(d.InvalidWorkoutVibes):
            _vibes()

    def test_unknown_rejected(self):
        with pytest.raises(d.InvalidWorkoutVibes):
            _vibes("Spelunking")


class TestSessionPayLater:
    def _build(self):
        return d.Session.create_pay_later(
            host_client_id=42,
            gym_id=7,
            session_date=date(2026, 5, 25),
            session_time=time(10, 30),
            mate_preference=d.MatePreference.MALE,
            fitness_level=d.FitnessLevel.INTERMEDIATE,
            workout_vibes=_vibes("Push Day", "HIIT"),
        )

    def test_creates_unpaid(self):
        s = self._build()
        assert s.payment_mode is d.PaymentMode.PAY_LATER
        assert s.payment_status is d.PaymentStatus.UNPAID
        assert s.status is d.SessionStatus.OPEN
        assert s.daily_pass_id is None

    def test_host_can_cancel(self):
        s = self._build()
        s.cancel(42)
        assert s.status is d.SessionStatus.CANCELLED

    def test_non_host_cannot_cancel(self):
        s = self._build()
        with pytest.raises(d.SessionNotOwned):
            s.cancel(99)

    def test_double_cancel_rejected(self):
        s = self._build()
        s.cancel(42)
        with pytest.raises(d.SessionAlreadyCancelled):
            s.cancel(42)

    def test_mark_paid_from_unpaid(self):
        s = self._build()
        s.mark_paid("dps_xyz")
        assert s.payment_status is d.PaymentStatus.PAID
        assert s.daily_pass_id == "dps_xyz"

    def test_mark_paid_twice_rejected(self):
        s = self._build()
        s.mark_paid("dps_xyz")
        with pytest.raises(d.SessionAlreadyPaid):
            s.mark_paid("dps_other")


class TestSessionPayNow:
    def test_creates_pending(self):
        s = d.Session.create_pay_now(
            host_client_id=42,
            gym_id=7,
            session_date=date(2026, 5, 25),
            session_time=time(10, 30),
            mate_preference=d.MatePreference.GROUP,
            fitness_level=d.FitnessLevel.ADVANCED,
            workout_vibes=_vibes("CrossFit"),
        )
        assert s.payment_mode is d.PaymentMode.PAY_NOW
        assert s.payment_status is d.PaymentStatus.PENDING

    def test_mark_paid_from_pending(self):
        s = d.Session.create_pay_now(
            host_client_id=42, gym_id=7,
            session_date=date(2026, 5, 25), session_time=time(10, 30),
            mate_preference=d.MatePreference.MALE,
            fitness_level=d.FitnessLevel.BEGINNER,
            workout_vibes=_vibes("Yoga"),
        )
        s.mark_paid("dps_xyz")
        assert s.payment_status is d.PaymentStatus.PAID
