'''String utility functions.'''

import re


_NON_ALPHANUMERIC_RE = re.compile(r'[^a-z0-9]+')


def slugify(text: str) -> str:
	'''Convert text into a URL-friendly slug.

	Lowercase the input, replace runs of non-alphanumeric characters with
	single hyphens, and strip leading/trailing hyphens.
	'''
	return _NON_ALPHANUMERIC_RE.sub('-', text.lower()).strip('-')
