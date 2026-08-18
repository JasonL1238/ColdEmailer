"""Which kind of email earns replies — and refusing to guess when it cannot.

The app could always say how many emails went out and how many came back. It
could never say which *kind* came back, so every send repeated the last one's
assumptions. These aggregates answer that.

Two rules run through every test here, and both are about not lying:

  * only replies the current checker verified count toward a rate — the legacy
    flags counted bounces, auto-replies and the user's own thread messages;
  * a rate needs a sample. At this app's volumes most segments are three or
    four sends, where one reply reads as 33%. Below MIN_SAMPLE the rate is
    None and the counts stand on their own.

The second rule is the one worth defending: a plausible number is more
dangerous than a missing one, because the user acts on it.
"""
import asyncio
import itertools
from datetime import datetime, timedelta

import analytics
import main

_seq = itertools.count()


def _rows(n, replied=0, unverified=0, **fields):
    """n sent emails, `replied` of them with a verified reply."""
    out = []
    for i in range(n):
        row = {"email_type": "application", "used_template_fallback": 0,
               "follow_up_step": 0, "has_response": 0, "response_verified_at": None,
               "sent_at": "2026-07-01T09:00:00", "response_at": None,
               "contact_email_kind": "personal", "seniority_rank": 20,
               "company_name": "ZZTEST Corp", **fields}
        if i < replied:
            row.update(has_response=1, response_at="2026-07-02T09:00:00",
                       response_verified_at="2026-07-02T09:05:00")
        elif i < replied + unverified:
            row.update(has_response=1, response_at="2026-07-02T09:00:00")
        out.append(row)
    return out


# ---------- a rate needs a sample ----------

def test_a_thin_segment_reports_counts_and_refuses_a_rate():
    """One reply out of two is not 50%. It is noise with a percent sign, and
    the user would act on it."""
    result = analytics.segment(_rows(2, replied=1), key=lambda r: r["email_type"])
    assert len(result) == 1
    assert result[0]["sent"] == 2 and result[0]["replied"] == 1
    assert result[0]["rate"] is None
    assert result[0]["enough_data"] is False


def test_a_segment_at_the_threshold_reports_a_rate():
    result = analytics.segment(_rows(analytics.MIN_SAMPLE, replied=3),
                               key=lambda r: r["email_type"])
    assert result[0]["enough_data"] is True
    assert result[0]["rate"] == round(3 / analytics.MIN_SAMPLE * 100, 1)

    one_short = analytics.segment(_rows(analytics.MIN_SAMPLE - 1, replied=3),
                                  key=lambda r: r["email_type"])
    assert one_short[0]["rate"] is None


def test_only_verified_replies_count_toward_a_rate():
    """The legacy checker counted bounces, auto-replies and our own messages.
    Those are surfaced separately as unverified; letting them into the
    numerator is how this app once claimed a ~90% reply rate."""
    rows = _rows(analytics.MIN_SAMPLE, replied=2, unverified=5)
    result = analytics.segment(rows, key=lambda r: r["email_type"])[0]
    assert result["replied"] == 2
    assert result["unverified"] == 5
    assert result["rate"] == round(2 / analytics.MIN_SAMPLE * 100, 1)


def test_a_bucket_key_of_none_is_dropped_not_labelled_unknown():
    """"No data" and "a group called unknown" are different claims. Mixing
    them makes every other segment's share of the total wrong."""
    rows = _rows(3) + _rows(2, email_type=None)
    result = analytics.segment(rows, key=lambda r: r.get("email_type") or None)
    assert [s["sent"] for s in result] == [3]


# ---------- comparing segments ----------

def test_no_best_or_worst_is_named_without_two_qualifying_segments():
    """Naming a winner out of one qualifying group just restates that group;
    out of zero it is a guess. Both read to the user as a finding."""
    thin = analytics.segment(_rows(3, replied=2), key=lambda r: r["email_type"])
    assert analytics.best_and_worst(thin)["best"] is None

    one_good = analytics.segment(
        _rows(analytics.MIN_SAMPLE, replied=5) + _rows(2, replied=2,
                                                       email_type="coffee_chat"),
        key=lambda r: r["email_type"])
    assert analytics.best_and_worst(one_good)["best"] is None


def test_best_and_worst_name_the_spread_when_the_evidence_supports_it():
    rows = (_rows(analytics.MIN_SAMPLE, replied=5, email_type="coffee_chat")
            + _rows(analytics.MIN_SAMPLE, replied=1, email_type="application"))
    verdict = analytics.best_and_worst(
        analytics.segment(rows, key=lambda r: r["email_type"]))
    assert verdict["best"]["key"] == "coffee_chat"
    assert verdict["worst"]["key"] == "application"
    assert verdict["spread"] == 40.0


# ---------- time to reply ----------

def test_time_to_reply_uses_verified_replies_only():
    rows = _rows(4, replied=1, unverified=2)
    assert len(analytics.hours_to_reply(rows)) == 1


def test_a_reply_timestamped_before_its_own_send_is_dropped():
    """A clock skew or a legacy import, not a fast answer. Showing it as a
    negative wait would drag the median somewhere no reply ever was."""
    rows = _rows(1, replied=1)
    rows[0]["response_at"] = "2026-06-01T09:00:00"      # before sent_at
    assert analytics.hours_to_reply(rows) == []


def test_the_distribution_describes_nothing_when_there_is_nothing():
    empty = analytics.distribution([])
    assert empty["count"] == 0
    assert all(empty[k] is None for k in ("median", "p25", "p75", "fastest", "slowest"))


def test_the_distribution_reports_the_median_not_the_mean():
    """One reply three weeks late would drag an average somewhere no
    individual reply ever was, and "when do I stop waiting" is the question."""
    values = [1.0, 2.0, 3.0, 4.0, 500.0]
    d = analytics.distribution(values)
    assert d["median"] == 3.0                 # mean would be 102
    assert d["fastest"] == 1.0 and d["slowest"] == 500.0
    assert d["count"] == 5


def test_quartiles_are_real_samples_rather_than_interpolations():
    """Nearest-rank: with a handful of points, interpolating invents a wait no
    reply actually had."""
    d = analytics.distribution([1.0, 2.0, 3.0, 4.0])
    for key in ("p25", "median", "p75"):
        assert d[key] in (1.0, 2.0, 3.0, 4.0)


# ---------- the whole payload ----------

def test_build_segments_every_dimension_that_can_change_a_decision():
    rows = (_rows(analytics.MIN_SAMPLE, replied=4, email_type="coffee_chat",
                  contact_email_kind="personal", seniority_rank=2)
            + _rows(analytics.MIN_SAMPLE, replied=0, email_type="application",
                    contact_email_kind="generic", seniority_rank=20,
                    used_template_fallback=1, follow_up_step=1))
    built = analytics.build(rows)

    assert built["sent"] == analytics.MIN_SAMPLE * 2
    assert built["replied"] == 4
    assert set(built["segments"]) == {
        "email_type", "email_kind", "seniority", "company", "written_by",
        "follow_up_step"}
    # the claim item 1 was built on, now measurable
    kinds = {s["key"]: s for s in built["segments"]["email_kind"]}
    assert kinds["personal"]["rate"] == 40.0
    assert kinds["generic"]["rate"] == 0.0
    assert "Role inbox" in kinds["generic"]["label"]
    # and the one item 3 was built on
    steps = {s["key"]: s for s in built["segments"]["follow_up_step"]}
    assert steps[0]["label"] == "First contact"
    assert steps[1]["label"] == "Follow-up 1"
    assert built["min_sample"] == analytics.MIN_SAMPLE


def test_seniority_bands_are_ordered_and_ignore_junk():
    assert analytics._seniority_band(1) == "Founder / C-level"
    assert analytics._seniority_band(5) == "VP / Director"
    assert analytics._seniority_band(12) == "Manager / Lead"
    assert analytics._seniority_band(20) == "Individual contributor"
    assert analytics._seniority_band(None) is None
    assert analytics._seniority_band("senior") is None


def test_build_survives_a_completely_empty_database():
    built = analytics.build([])
    assert built["sent"] == 0 and built["replied"] == 0
    assert built["time_to_reply_hours"]["count"] == 0
    assert all(v == [] for v in built["segments"].values())
    assert built["headline"]["email_type"]["best"] is None


# ---------- the endpoint ----------

def test_the_endpoint_counts_only_delivered_mail_inside_the_window():
    """Drafts and trashed rows were never received by anyone, and a 90-day
    view that silently included a two-year-old campaign would describe a
    strategy the user has already moved on from."""
    seq = next(_seq)
    contact = main.db.create_contact(name=f"ZZTEST An {seq}",
                                     email=f"zztestan{seq}@example.com")
    recent = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    try:
        main.db.create_email(contact_id=contact["id"], status="sent",
                             subject="ZZTEST in", body="hi", sent_at=recent)
        main.db.create_email(contact_id=contact["id"], status="sent",
                             subject="ZZTEST old", body="hi", sent_at=old)
        main.db.create_email(contact_id=contact["id"], status="draft",
                             subject="ZZTEST draft", body="hi")
        main.db.create_email(contact_id=contact["id"], status="trashed",
                             subject="ZZTEST binned", body="hi")

        window = asyncio.run(main.analytics(days=90))
        assert window["days"] == 90
        forever = asyncio.run(main.analytics(days=730))
        assert forever["sent"] == window["sent"] + 1
    finally:
        main.db.delete_contact(contact["id"])


# ---------- what the first adversarial round found ----------

def test_a_follow_up_of_unknown_rung_is_dropped_not_called_first_contact():
    """`follow_up_step` arrived with the cadence feature and its migration
    backfilled every existing row with 0, so a follow-up sent before that
    column existed is indistinguishable from a first contact by step alone.
    Folding them together did not just mislabel: it merged a chased population
    into an un-chased one and reported a rate for the mixture, which is the
    exact thing this module exists not to do."""
    rows = (_rows(6, replied=2)                                  # real first contacts
            + _rows(6, replied=0, is_follow_up=1))               # legacy follow-ups
    steps = analytics.build(rows)["segments"]["follow_up_step"]

    assert [s["key"] for s in steps] == [0]
    assert steps[0]["sent"] == 6 and steps[0]["replied"] == 2
    assert steps[0]["enough_data"] is False        # 6 < MIN_SAMPLE, so no rate
    assert steps[0]["dropped"] == 6

    # an original_email_id alone is enough evidence that it is a follow-up
    only_parent = analytics.build(_rows(4, original_email_id="root"))
    assert only_parent["segments"]["follow_up_step"] == []


def test_a_known_rung_still_reports_normally():
    rows = _rows(4, follow_up_step=2, is_follow_up=1) + _rows(4)
    steps = {s["key"]: s for s in analytics.build(rows)["segments"]["follow_up_step"]}
    assert steps[0]["label"] == "First contact"
    assert steps[2]["label"] == "Follow-up 2"


def test_two_companies_sharing_a_name_stay_separate(db):
    """This app deliberately keeps same-named companies on different domains as
    distinct rows. Grouping the segment by name merged their sends into one
    bucket that cleared the sample floor neither of them reached."""
    rows = (_rows(6, replied=1, company_id="c-a", company_name="ZZTEST Atlas")
            + _rows(6, replied=1, company_id="c-b", company_name="ZZTEST Atlas"))
    companies = analytics.build(rows)["segments"]["company"]

    assert len(companies) == 2
    assert all(c["sent"] == 6 and c["enough_data"] is False for c in companies)
    assert all(c["label"] == "ZZTEST Atlas" for c in companies)


def test_the_percentile_index_has_one_convention_at_every_length():
    """`round()` is banker's rounding, so the tie between the two middle
    samples broke upward or downward with the parity of the index — four
    replies gave a "median" with three of them at or below it, six gave the
    lower middle. Same word, two meanings."""
    assert analytics.distribution([1.0, 2.0])["median"] == 2.0
    assert analytics.distribution([1.0, 2.0, 3.0, 4.0])["median"] == 3.0
    assert analytics.distribution([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])["median"] == 4.0
    # ...and half-up is applied consistently, not per-parity
    for n in range(2, 12):
        values = [float(i) for i in range(1, n + 1)]
        d = analytics.distribution(values)
        assert d["p25"] <= d["median"] <= d["p75"]


def test_one_rounding_implementation_serves_the_page_and_the_segments():
    """JS rounds halves up, Python rounds them to even, so 1 of 16 was 6.3% in
    the headline tile and 6.2% in the segment card directly beneath it."""
    rows = _rows(16, replied=1)
    built = analytics.build(rows)
    assert built["rate"] == analytics.rate_of(1, 16) == 6.2
    assert built["segments"]["email_type"][0]["rate"] == built["rate"]
    # ...and the overall rate is withheld on the same floor as any segment
    assert analytics.build(_rows(4, replied=2))["rate"] is None


def test_replies_with_unusable_timestamps_are_counted_and_reported():
    """Dropping them from the wait is right; letting the page then say "no
    replies yet" beside "Replied 5" is not."""
    rows = _rows(12, replied=5)
    for row in rows[:5]:
        row["response_at"] = "2020-01-01T00:00:00"      # before its own send
    built = analytics.build(rows)
    assert built["replied"] == 5
    assert built["time_to_reply_hours"]["count"] == 0
    assert built["time_to_reply_hours"]["excluded"] == 5


def test_a_segment_reports_how_many_rows_it_could_not_place():
    """Otherwise a card whose rows all lack a company reads "nothing sent in
    this segment", while the headline says forty."""
    rows = _rows(12, company_id=None, company_name=None)
    assert analytics.build(rows)["segments"]["company"] == []
    mixed = analytics.build(_rows(4, company_id="c-a") + _rows(8, company_id=None))
    assert mixed["segments"]["company"][0]["dropped"] == 8
