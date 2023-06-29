#!/usr/bin/env python3
import datetime
import json
import os
import pathlib
import re
import sys
from bisect import bisect_right
from functools import reduce
from itertools import chain

from requests import post

WEIGHT_ACTIVITY = float(os.environ.get("WEIGHT_ACTIVITY", 14) or 14)
WEIGHT_REACTIONS = float(os.environ.get("WEIGHT_REACTIONS", 7) or 7)
WEIGHT_STALENESS = float(os.environ.get("WEIGHT_STALENESS", 1) or 1)
WEIGHT_AGE = float(os.environ.get("WEIGHT_AGE", 0.142857) or 0.142857)  # 1/7
MULTIPLIER_PR = float(os.environ.get("MULTIPLIER_PR", 7) or 7)
MULTIPLIER_LABELS = "example:-1 epic:0.142857 blocked:0.142857 invalid:0.142857"
MULTIPLIER_LABELS = os.environ.get("MULTIPLIER_LABELS", MULTIPLIER_LABELS) or MULTIPLIER_LABELS
MULTIPLIER_LABELS = dict(i.rsplit(":", 1) for i in MULTIPLIER_LABELS.split())
MULTIPLIER_LABELS = {k: float(v) for k, v in MULTIPLIER_LABELS.items()}
P_LABEL_GRAVEYARD = float(os.environ.get("P_LABEL_GRAVEYARD", 4) or 4)
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
GITHUB_SERVER_URL = os.environ.get("GITHUB_SERVER_URL", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
NOW = datetime.datetime.now()

try:
    with open("people.json") as fd:
        PEOPLE = json.load(fd)
except FileNotFoundError:
    PEOPLE = {}


def age_days(t):
    return (NOW - datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")).days


def label_priority(label):
    if p := re.search(r"[pP](?:riority)[\s:-]*([0-9]+).*", label):
        return P_LABEL_GRAVEYARD - int(p.group(1))
    if re.search("bug|external-request", label, flags=re.I):
        return P_LABEL_GRAVEYARD - 1  # same as p1-important
    return 0  # default: no contribution


def priority(issue):
    return (
        (
            WEIGHT_REACTIONS * sum(r["users"]["totalCount"] for r in issue["reactionGroups"])
            + WEIGHT_STALENESS * age_days(issue["updatedAt"])
            + WEIGHT_AGE * age_days(issue["createdAt"])
            + WEIGHT_ACTIVITY * len(issue["comments"])
        )
        * (
            sum(label_priority(lab["name"]) for lab in issue["labels"])
            or P_LABEL_GRAVEYARD - 0  # default: p0-unlabelled
        )
        * reduce(
            lambda x, y: x * y,
            (MULTIPLIER_LABELS.get(lab["name"], 1) for lab in issue["labels"]),
            1,
        )
        * (MULTIPLIER_PR if issue["url"].rsplit("/", 2)[-2] == "pull" else 1)
    )


def prettify_link(issue, slack=False):
    url = issue["url"]
    title = issue["title"]
    pretty = url.split("/", 3)[-1].replace("/issues/", "#").replace("/pull/", "#")
    return f"<{url}|{title}>" if slack else f"[{pretty}]({url})"


def assigned(issue, slack=False):
    return " ".join(
        (
            f"<@{user}>"
            for user in filter(None, (PEOPLE.get(i["login"], "") for i in issue["assignees"]))
        )
        if slack
        else (f"@{i['login']}" for i in issue["assignees"])
    )


if __name__ == "__main__":
    issues = sum((json.load(d.open()) for d in pathlib.Path(".").glob("*s.*-*.json")), [])
    issues.sort(key=priority, reverse=True)
    # drop excluded (more efficient version uses `bisect`)
    # while issues and priority(issues[-1]) < 0: issues.pop()
    issues = issues[: len(issues) - bisect_right(issues[::-1], 0, key=priority)]
    assert not issues or priority(issues[-1]) >= 0

    source_link = (
        f"{GITHUB_SERVER_URL}/{GITHUB_REPOSITORY}/actions/runs/{GITHUB_RUN_ID}"
        if GITHUB_SERVER_URL and GITHUB_REPOSITORY and GITHUB_RUN_ID
        else "https://github.com/iterative/priority-list#notes"
    )
    print(f"#|[priority]({source_link})|days stale|link|assigned")
    print("-:|-:|-:|:-|:-")
    N = len(issues)
    slack_md = f":fire: *<{source_link}|priority>*"
    slack_md += " :calendar: days stale\n"
    for i in chain(range(min(10, N)), [None], range(N - 5, N)):
        if i is None:
            if N <= 15:
                break
            print("...|...|...|...|...")
            slack_md += "...\n"
        else:
            print(
                f"{i}|{priority(issues[i]):.0f}|{age_days(issues[i]['updatedAt'])}"
                f"|{prettify_link(issues[i])}|{assigned(issues[i])}"
            )
            slack_md += (
                f":fire: {priority(issues[i]):.0f} :calendar: {age_days(issues[i]['updatedAt'])}"
                f" {prettify_link(issues[i], True)} {assigned(issues[i], slack=True)}"
                "\n"
            )
    slack_payload = {
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": slack_md.rstrip()}}]
    }
    if SLACK_WEBHOOK:
        post(SLACK_WEBHOOK, json=slack_payload)
    else:
        print(json.dumps(slack_payload), file=sys.stderr)
