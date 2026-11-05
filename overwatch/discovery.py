"""Crustdata-backed provider discovery for the tender engine.

Discovery is NOT qualification. Every Crustdata lead is *indicative*: a candidate to
invite to pre-qualification, never an available or qualified supplier (PRD: "Crustdata
data never makes a provider available or qualified"). Only typed fields are kept; the
free-text company description is dropped so untrusted external prose never reaches a
tool-using agent (PRD: the quarantined market reader "outputs typed fields only").

The live client uses only the standard library. The demo and tests run without a token
through a fixture client, so the no-dependency, no-account invariant of the repo holds.
"""
import json
import urllib.error
import urllib.request

from .errors import Rejected
from .store import digest

CRUSTDATA_SEARCH_URL = "https://api.crustdata.com/company/search"
CRUSTDATA_API_VERSION = "2025-11-01"


class DiscoveryError(Rejected):
    """A discovery source was unreachable or returned an unusable payload."""


def _normalized_key(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


class CrustdataClient:
    """Thin read-only REST client for Crustdata company search. stdlib only.

    The token comes from configuration (never a request body). The client returns raw
    company records; normalization and the indicative-only contract are enforced here
    by ``normalize_lead``, not by the caller.
    """
    source = "crustdata.company_search"

    def __init__(self, token, *, url=CRUSTDATA_SEARCH_URL, timeout=20, opener=None):
        if not token:
            raise Rejected("Crustdata token required for live discovery")
        self.token, self.url, self.timeout = token, url, timeout
        self._opener = opener or urllib.request.urlopen

    def search_companies(self, query, *, limit=25):
        body = json.dumps({"search": {"query": query, "mode": "semantic"},
                           "limit": max(1, min(int(limit), 100))}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token,
            "x-api-version": CRUSTDATA_API_VERSION})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()  # close the wrapped socket so it cannot leak a ResourceWarning
            raise DiscoveryError("Crustdata unavailable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("companies"), list):
            raise DiscoveryError("unexpected Crustdata response")
        return payload["companies"]


def normalize_lead(company, *, now):
    """Keep only typed, non-free-text fields. Mark the lead indicative and unqualified."""
    if not isinstance(company, dict):
        raise DiscoveryError("malformed company record")
    basic = company.get("basic_info") or {}
    locations = company.get("locations") or {}
    name = basic.get("name") or company.get("name")
    if not name:
        raise DiscoveryError("company record missing name")
    lead = {
        "crustdata_company_id": company.get("crustdata_company_id") or basic.get("crustdata_company_id"),
        "name": name,
        "domain": basic.get("primary_domain") or basic.get("website"),
        "hq_country": locations.get("country"),
        "hq_city": locations.get("city"),
        "employee_count_range": basic.get("employee_count_range"),
        "year_founded": basic.get("year_founded"),
        "status": basic.get("status"),
        "source": CrustdataClient.source,
        "authority": "indicative",   # never authoritative for availability, price or qualification
        "qualified": False,          # discovery alone cannot qualify a supplier
        "retrieved_at": now,
    }
    lead["id"] = "lead_" + digest(lead)[:16]
    return lead


def discover_providers(need, *, approved_vendors=None, client=None, now,
                       blocked=None, sanctioned=None, limit=25):
    """Build an indicative long-list: approved vendors first, then Crustdata leads.

    Sanctioned and blocked suppliers are removed with a logged reason. If Crustdata is
    unreachable the shortlist continues from the approved vendor list only and the gap
    is stated; nothing is ever guessed in to fill it (PRD edge case).
    """
    if not isinstance(need, dict):
        raise Rejected("need must be an object")
    query = need.get("query")
    if not isinstance(query, str) or not query.strip():
        raise Rejected("need.query is required for discovery")
    blocked_keys = {_normalized_key(x) for x in (blocked or [])}
    sanctioned_keys = {_normalized_key(x) for x in (sanctioned or [])}
    leads, screened_out, seen = [], [], set()

    def admit(lead, origin):
        name_key, domain_key = _normalized_key(lead.get("name")), _normalized_key(lead.get("domain"))
        dedupe_key = domain_key or name_key
        if not dedupe_key or dedupe_key in seen:
            return
        seen.add(dedupe_key)
        reasons = []
        if name_key in sanctioned_keys or (domain_key and domain_key in sanctioned_keys):
            reasons.append("sanctions/entity-list match")
        if name_key in blocked_keys or (domain_key and domain_key in blocked_keys):
            reasons.append("customer blocked-supplier list")
        if reasons:
            screened_out.append({"name": lead.get("name"), "origin": origin, "reason": "; ".join(reasons)})
        else:
            leads.append({**lead, "origin": origin})

    for vendor in (approved_vendors or []):
        name = vendor.get("name") if isinstance(vendor, dict) else vendor
        domain = vendor.get("domain") if isinstance(vendor, dict) else None
        if not name:
            raise Rejected("approved vendor requires a name")
        admit({"id": "lead_av_" + digest([name, domain])[:12], "name": name, "domain": domain,
               "source": "customer.approved_vendor_list", "authority": "approved_vendor",
               "qualified": False, "retrieved_at": now}, "approved_vendor")

    degraded, source_note = False, "Crustdata discovery active"
    if client is None:
        degraded, source_note = True, "No discovery client configured; approved vendors only"
    else:
        try:
            for company in client.search_companies(query, limit=limit):
                admit(normalize_lead(company, now=now), "crustdata")
        except DiscoveryError as exc:
            degraded = True
            source_note = "Crustdata unavailable; shortlist built from approved vendors only (" + str(exc) + ")"

    return {
        "need_query": query,
        "leads": leads,
        "screened_out": screened_out,
        "degraded": degraded,
        "source_note": source_note,
        "methodology": ("Approved vendors loaded first; Crustdata leads are indicative and "
                        "must pass pre-qualification; sanctioned/blocked removed with a reason"),
    }


class FixtureDiscoveryClient:
    """Keyless client returning real captured third-party-maintainer records.

    Field shapes mirror a live Crustdata ``company/search`` response so the demo runs
    without a token while exercising the same normalization path. Free-text company
    descriptions are intentionally excluded from the fixture, as they are from leads.
    """
    source = CrustdataClient.source
    SAMPLE = [
        {"crustdata_company_id": 1998628, "basic_info": {
            "name": "Evernex", "primary_domain": "evernex.com", "status": "active",
            "employee_count_range": "501-1000", "year_founded": 1983},
         "locations": {"country": "FRA", "city": "Paris"}},
        {"crustdata_company_id": 1958775, "basic_info": {
            "name": "SSCS", "primary_domain": "sscs.com", "status": "active",
            "employee_count_range": "201-500", "year_founded": 1988},
         "locations": {"country": "USA", "city": "Houston"}},
        {"crustdata_company_id": 1484621, "basic_info": {
            "name": "Top Ten USA, LLC.", "primary_domain": "toptenusa.com", "status": "active",
            "employee_count_range": "11-50", "year_founded": 2007},
         "locations": {"country": "USA", "city": "Mount Olive"}},
    ]

    def search_companies(self, query, *, limit=25):
        return self.SAMPLE[:max(1, min(int(limit), 100))]
