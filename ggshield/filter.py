import hashlib
import math
import operator
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Set

from pygitguardian.models import Match, PolicyBreak, ScanResult

from .git_shell import is_git_dir, shell


REGEX_MATCH_HIDE = re.compile(r"[^+\-\s]")

MAXIMUM_CENSOR_LENGTH = 60


def is_ignored(policy_break: PolicyBreak, matches_ignore: Iterable[str]) -> bool:
    """
    is_ignored checks if a policy break is ignored.
    There are 2 ways of ignoring a policy break:
    - matching the policy break sha
    - matching one of the match.match values

    :param policy_break: Policy Break to judge
    :param matches_ignore: Iterable of match ignores (plaintext secrets of SHAs)
    :return: True if ignored
    """
    if get_ignore_sha(policy_break) in matches_ignore or any(
        match.match in matches_ignore for match in policy_break.matches
    ):
        return True
    return False


def remove_ignored_from_result(scan_result: ScanResult, matches_ignore: Iterable[str]):
    """
    remove_ignored removes policy breaks from a Scan Result based on a sha
    made from its matches.

    :param scan_result: ScanResult to filter
    :param matches_ignore: match SHAs or plaintext matches to filter out
    """

    scan_result.policy_breaks = [
        policy_break
        for policy_break in scan_result.policy_breaks
        if not is_ignored(policy_break, matches_ignore)
    ]

    scan_result.policy_break_count = len(scan_result.policy_breaks)


def get_ignore_sha(policy_break: PolicyBreak):
    hashable = "".join(
        [
            f"{match.match},{match.match_type}"
            for match in sorted(
                policy_break.matches, key=operator.attrgetter("match_type")
            )
        ]
    )

    return hashlib.sha256(hashable.encode("UTF-8")).hexdigest()


def leak_dictionary_by_ignore_sha(
    policy_breaks: List[PolicyBreak],
) -> Dict[str, List[PolicyBreak]]:
    """
    leak_dictionary_by_ignore_sha sorts matches and policy breaks by
    first appearance in file.

    sort policy breaks by first appearance on file,
    file wide matches have no index
    so give it -1 so they get bumped to the top

    :return: Dictionary with line number as index and a list of
    matches that start on said line.
    """
    policy_breaks.sort(
        key=lambda x: min(
            (match.index_start if match.index_start else -1 for match in x.matches)
        )
    )
    sha_dict = OrderedDict()
    for policy_break in policy_breaks:
        policy_break.matches.sort(key=lambda x: x.index_start if x.index_start else -1)
        ignore_sha = get_ignore_sha(policy_break)
        sha_dict.setdefault(ignore_sha, []).append(policy_break)

    return sha_dict


def path_filter_set(top_dir: Path, paths_ignore: Iterable[str]) -> Set[str]:
    """
    filter_set creates a set of paths of the ignored
    entries from 3 sources:
    .gitguardian.yaml
    files in .git
    files ignore in .gitignore
    """
    filters = set()
    for ignored in paths_ignore:
        filters.update({str(target) for target in top_dir.glob(ignored)})

    if is_git_dir():
        filters.update({str(target) for target in top_dir.glob(r".git/**/*")})
        filters.update(
            {
                os.path.join(top_dir, filename)
                for filename in shell(
                    ["git", "ls-files", "-o", "-i", "--exclude-standard"]
                )
            }
        )
    return filters


def censor_match(match: Match) -> str:
    """
    censored_match censors a match value revealing only the first and last
    1/6th of the match up to a maximum of MAXIMUM_CENSOR_LENGTH.

    :return: match value censored
    """
    len_match = len(match.match)
    start_privy_len = min(math.ceil(len_match / 6), MAXIMUM_CENSOR_LENGTH)
    end_privy_len = len_match - min(math.ceil(len_match / 6), MAXIMUM_CENSOR_LENGTH)

    censored = REGEX_MATCH_HIDE.sub("*", match.match)

    return (
        match.match[:start_privy_len]
        + censored[start_privy_len:end_privy_len]
        + match.match[end_privy_len:]
    )


def censor_content(content: str, policy_breaks: List[PolicyBreak]) -> str:
    for policy_break in policy_breaks:
        for match in policy_break.matches:
            if not match.index_start:
                continue

            match.match = censor_match(match)
            content = "".join(
                (
                    content[: match.index_start],
                    match.match,
                    content[len(match.match) + match.index_start :],
                )
            )
    return content
