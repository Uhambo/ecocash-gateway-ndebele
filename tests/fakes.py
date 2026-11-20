"""A pretend EcoCash that follows the sandbox's observed behaviour. No network is used."""
import requests

EIP = "https://eip.fake/v1"
EPG = "https://epg.fake/serviceRequest"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


class FakeEcoCash:
    def __init__(self):
        self.calls = []            # every request, for assertions
        self.eip_accounts = {}     # username -> password
        self.epg_clients = {}      # client_id -> client_secret
        self.whitelist = {"263770000000"}
        self.eip = {}              # correlator -> record
        self.epg = {}              # externalReference -> transactionId
        self.timeout_before = False  # fail without processing
        self.timeout_after = False   # process, then lose the response
        self._n = 0

    def _id(self, prefix):
        self._n += 1
        return f"{prefix}.{self._n:04d}"

    def settle(self, correlator, status):
        """Simulates the customer entering a PIN: COMPLETED or FAILED."""
        self.eip[correlator]["transactionOperationStatus"] = status
        self.eip[correlator]["ecocashReference"] = self._id("MP")

    def request(self, method, url, json=None, auth=None, headers=None, timeout=None, **_):
        self.calls.append({"method": method, "url": url, "json": json, "auth": auth, "headers": headers})
        if self.timeout_before:
            raise requests.ConnectTimeout()
        response = self._eip(method, url, json, auth) if url.startswith(EIP) else self._epg(json, headers or {})
        if self.timeout_after:
            raise requests.ReadTimeout()
        return response

    def _eip(self, method, url, body, auth):
        if not auth or self.eip_accounts.get(auth[0]) != auth[1]:
            return FakeResponse(401, {"message": "Unauthorized"})
        if method == "GET":
            record = self.eip.get(url.rsplit("/", 1)[1])
            return FakeResponse(200, record) if record else FakeResponse(404, {"message": "Not found"})
        if url.endswith("/refund/"):
            return FakeResponse(200, {"clientCorrelator": body["clientCorrelator"],
                                      "transactionOperationStatus": "COMPLETED", "ecocashReference": self._id("RF")})
        record = {"clientCorrelator": body["clientCorrelator"], "endUserId": body["endUserId"],
                  "notifyUrl": body["notifyUrl"], "transactionOperationStatus": "PENDING SUBSCRIBER VALIDATION"}
        self.eip[body["clientCorrelator"]] = record
        return FakeResponse(200, dict(record))

    def _epg(self, body, headers):
        if self.epg_clients.get(headers.get("X-Client-Id")) != headers.get("X-Client-Secret"):
            return FakeResponse(401, {"responseCode": "SBX40100", "message": "Bad client credentials"})
        service = body["serviceType"]
        if service == "EXTERNALTRANSACTIONLOOKUP":
            txn_id = self.epg.get(body["externalTransactionId"])
            if not txn_id:
                return FakeResponse(400, {"responseCode": "888803", "message": "No Transaction Found"})
            return FakeResponse(200, {"responseCode": "00", "transactionId": txn_id, "statusCode": "TS"})
        receiver = body.get("msisdn") or body.get("receiverNumber")
        channel = {"ENTERPRISECASHINV2": "700002", "CHANGEDISBURSEV2": "800002"}[service]
        if body["channelCode"] != channel:
            return FakeResponse(400, {"responseCode": "TP40288", "message": "The specified number does not exist"})
        if receiver not in self.whitelist:
            return FakeResponse(400, {"responseCode": "IC50103", "message": "Invalid receiver in transaction."})
        if body["externalReference"] not in self.epg:
            self.epg[body["externalReference"]] = self._id("CI")
        return FakeResponse(200, {"responseCode": "00", "transactionId": self.epg[body["externalReference"]],
                                  "responseMessage": "ok"})

    def money_calls(self):
        return [c for c in self.calls if c["method"] == "POST"
                and (c["json"] or {}).get("serviceType") != "EXTERNALTRANSACTIONLOOKUP"]


class FakeMerchantServer:
    """Collects the webhooks the gateway sends to merchants."""

    def __init__(self):
        self.received = []

    def request(self, method, url, data=None, headers=None, **_):
        self.received.append({"url": url, "body": data, "headers": headers})
        return FakeResponse(200, {})


def fake_resolver(host, port, *_):
    private = {"evil.internal": "10.0.0.5", "localhost": "127.0.0.1", "metadata.internal": "169.254.169.254"}
    if host in private:
        return [(2, 1, 6, "", (private[host], port))]
    if host.endswith(".example"):
        return [(2, 1, 6, "", ("93.184.216.34", port))]
    raise OSError("no such host")
