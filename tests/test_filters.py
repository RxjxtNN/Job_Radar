"""
Tests for filters.py — India location and fresher-suitability rules.

Cases are drawn from real titles seen in the Greenhouse / Lever caches that
the original keyword filter got wrong.
"""
from __future__ import annotations

import pytest


import filters


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("loc", [
    "Bengaluru, India", "Bangalore", "Remote - India", "Gurugram, Haryana, India",
    "Mumbai / Bangalore", "IN-Bengaluru", "Remote, Canada; Remote, India",
])
def test_india_locations_kept(loc):
    assert filters.is_india_location(loc)


@pytest.mark.parametrize("loc", [
    "Remote - USA", "Remote - Mexico", "London, UK", "San Francisco, CA", "",
])
def test_foreign_locations_rejected(loc):
    assert not filters.is_india_location(loc)


def test_bare_remote_needs_india_context():
    assert not filters.is_india_location("Remote", "Strategic Account Executive, France\n...")
    assert not filters.is_india_location("Remote", "Software Engineer\nWork from anywhere.")
    assert filters.is_india_location("Remote", "Software Engineer\nOpen to candidates in India.")


def test_bare_remote_with_foreign_title_rejected_even_if_india_in_desc():
    ctx = "Renewals Manager - Italy/ Spain\nWe also have offices in India."
    assert not filters.is_india_location("Remote", ctx)


# ---------------------------------------------------------------------------
# Fresher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Software Engineer, Intern",
    "Product Management Intern",
    "Payroll Industrial Trainee",
    "Operations Associate, Apprenticeship",
    "Early Talent Recruiter",
    "Assistant Manager - Business Finance | CA pass -out 2026",  # hard entry beats "manager"
    "Senior Software Engineer Intern",  # internship wins over "senior"
])
def test_hard_entry_titles_kept(title):
    assert filters.is_fresher_post(title, "5+ years of experience preferred")


@pytest.mark.parametrize("title", [
    "Senior Software Engineer",
    "Sr. Analyst, FP&A",
    "Staff Data Analyst",
    "Engineering Manager",
    "Lead UX Designer",
    "Software Engineer 3",
    "SDE II - AI",
    "Machine Learning Engineer II - Backend",
    "Intermediate Backend Engineer, India",
    "Strategic Account Executive",
    "Enterprise Account Executive (BFSI)",
    "Principal Solutions Architect",
])
def test_senior_titles_rejected(title):
    assert not filters.is_fresher_post(title, "")


def test_generic_title_with_experience_requirement_rejected():
    desc = "You have 3+ years of experience building distributed systems."
    assert not filters.is_fresher_post("Software Engineer", desc)


def test_associate_with_experience_requirement_rejected():
    desc = "Minimum of 4 years experience in risk operations."
    assert not filters.is_fresher_post("Credit Risk Operations Associate", desc)


def test_generic_title_without_requirement_kept_as_candidate():
    assert filters.is_fresher_post("Graphic Designer", "Design social creatives for our app.")


def test_description_signal_keeps_post():
    desc = "This role is ideal for freshers and recent graduates (0-1 years)."
    assert filters.is_fresher_post("Operations", desc)


def test_non_ic_title_without_signal_rejected():
    assert not filters.is_fresher_post("General Counsel", "Lead our legal function.")


@pytest.mark.parametrize("word", ["internal", "internet", "international"])
def test_intern_substring_not_matched(word):
    assert not filters.is_fresher_post(f"{word.title()} Communications Manager", "")


# ---------------------------------------------------------------------------
# Experience parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desc, expected", [
    ("5+ years of experience in sales", 5),
    ("2-4 yrs experience", 2),
    ("At least 3 years of hands-on experience", 3),
    ("0-1 years experience; freshers welcome", 0),
    ("Experience: 1 to 3 years", 1),
    ("Founded 10 years ago, we build payments.", None),
    ("B. Tech 5-8 years", 5),
    ("We have served customers for 3+ years", 3),
    ("No experience needed.", None),
])
def test_required_min_years(desc, expected):
    assert filters.required_min_years(desc) == expected


def test_internal_test_postings_rejected():
    assert not filters.is_fresher_post("Test Employee - Executive", "")


# Fresher = 0–2 years of experience
@pytest.mark.parametrize("desc", [
    "2 years of experience in customer support",
    "1-2 years experience with SQL",
    "2-4 years of experience preferred",   # range starts within 0–2
    "0-2 years experience",
])
def test_up_to_two_years_is_fresher(desc):
    assert filters.is_fresher_post("Data Analyst", desc)


@pytest.mark.parametrize("desc", [
    "3+ years of experience",
    "Minimum 3 years experience",
    "B.Tech with 3-5 years",
])
def test_three_plus_years_not_fresher(desc):
    assert not filters.is_fresher_post("Data Analyst", desc)


@pytest.mark.parametrize("title, expected", [
    ("IN_RBAI_Maintenance Engineer_Charcon_Mechanical", True),   # '_' no longer hides "engineer"
    ("IN_RBIN_Senior Engineer_Product Development", False),      # ...or "senior"
    ("Field Service Officer", True),
    ("Receptionist", True),
    ("Payroll Accountant", True),
    ("Employee Referral", False),
    ("Don't see what you're looking for?", False),
    ("Test Jerin", False),
])
def test_title_coverage(title, expected):
    assert filters.is_fresher_post(title, "Graduates welcome.") is expected
