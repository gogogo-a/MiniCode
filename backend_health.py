#!/usr/bin/env python3
'''Small backend health-check helper.'''

from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any


def build_health_response(service_name: str, *, dependencies: dict[str, bool] | None = None) -> dict[str, Any]:
	'''Build a consistent health-check response payload.'''
	dependencies = dependencies or {}
	is_healthy = all(dependencies.values()) if dependencies else True

	return {
		'service': service_name,
		'status': 'ok' if is_healthy else 'degraded',
		'status_code': HTTPStatus.OK if is_healthy else HTTPStatus.SERVICE_UNAVAILABLE,
		'checked_at': datetime.now(timezone.utc).isoformat(),
		'dependencies': dependencies,
	}


def is_healthy(response: dict[str, Any]) -> bool:
	'''Return True when a health response represents a healthy service.'''
	return response.get('status') == 'ok'


if __name__ == '__main__':
	print(build_health_response('backend'))
