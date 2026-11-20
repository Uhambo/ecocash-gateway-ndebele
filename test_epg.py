"""
EcoCash Payment Gateway sandbox scenarios.

Runs the three portal scenarios (Enterprise cash-in, Change disbursement,
Look up the cash-in) plus the negative cases each guide asks for.
Every request is recorded by the sandbox for the production access request.

Usage:
    python test_epg.py            # required scenarios + negative cases
    python test_epg.py --required # only the three portal scenarios
"""
import json
import os
import sys

from epg_client import EPGClient, new_reference

LOG_FILE = "epg_scenarios.log"


def log(msg, fh):
    print(msg)
    fh.write(msg + "\n")


def check(name, result, status, expected_code, fh):
    code = result.get("responseCode") or result.get("code")
    passed = str(code) == expected_code
    log(f"\n{'=' * 50}\n{name}\nExpected responseCode {expected_code}, got {code} (HTTP {status})"
        f" -> {'PASS' if passed else 'FAIL'}", fh)
    log(json.dumps(result, indent=2), fh)
    return passed


def main():
    required_only = "--required" in sys.argv
    msisdn = os.getenv("EPG_TEST_MSISDN")
    if not msisdn:
        sys.exit("Set EPG_TEST_MSISDN in .env to your whitelisted, OTP-verified test number.")

    client = EPGClient()
    results = []

    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        # 1. Enterprise cash-in (expect success)
        cash_in_ref = new_reference()
        r, s = client.enterprise_cash_in(msisdn, amount=1, external_reference=cash_in_ref)
        results.append(check("Enterprise cash-in", r, s, "00", fh))

        # 2. Change disbursement (expect success)
        r, s = client.change_disbursement(msisdn, amount=2)
        results.append(check("Change disbursement", r, s, "00", fh))

        # 3. Look up the cash-in by its externalReference (expect success)
        r, s = client.transaction_lookup(cash_in_ref, msisdn)
        results.append(check("Look up the cash-in", r, s, "00", fh))

        if not required_only:
            r, s = client.enterprise_cash_in(msisdn, amount=1, channel_code="999999")
            results.append(check("Cash-in, wrong channel code", r, s, "TP40288", fh))

            r, s = client.enterprise_cash_in("263770000001", amount=1)
            results.append(check("Cash-in, number not whitelisted", r, s, "IC50103", fh))

            r, s = client.change_disbursement(msisdn, amount=2, channel_code="999999")
            results.append(check("Change, wrong channel code", r, s, "TP40288", fh))

            r, s = client.transaction_lookup("does-not-exist-" + new_reference(), msisdn)
            results.append(check("Lookup, unknown reference", r, s, "888803", fh))

        log(f"\n{sum(results)}/{len(results)} passed. Full log: {LOG_FILE}", fh)

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
