# Contributing (a.k.a. The Rules, Read Them, I'm Not Joking)

Welcome. You have **write access** to a **payment gateway**. Software that moves people's money.
Let that sink in. Take a breath. Maybe drink some water. Now read every word below, because
"I didn't know" stopped being an excuse the moment you opened this file.

## The Ten Commandments

1. **Thou shalt not commit `.env`.** It's in `.gitignore`. If you `git add -f .env`, you will be
   forced to explain yourself in front of everyone, slowly, with diagrams.
2. **Thou shalt not commit real phone numbers.** Not yours, not the client's, not your cousin's
   "just for testing". Use `263770000000`. It belongs to nobody. That is its entire job.
3. **Thou shalt not commit real EcoCash references** (`MP...`, `CI...`) from sandbox runs. Logs are
   gitignored for a reason. Do not "just paste one into a test real quick".
4. **Thou shalt not push to `main` with failing tests.** Run them. They take 10 seconds. You've spent
   longer choosing a playlist.
5. **Thou shalt not log secrets.** No `print(creds)`. No `print(request.json())` "for debugging".
   Debug in your head like an adult, or log the *type* of the thing, never the thing.
6. **Thou shalt not touch money logic without a test that proves it.** Charges, refunds, payouts,
   idempotency, limits. New behaviour = new test. No test = no merge. No exceptions. No, not even you.
7. **Thou shalt not trust EcoCash callbacks.** The gateway asks EcoCash for the real status. If you
   "simplify" that by trusting the callback body, someone will forge a callback and get free stuff,
   and it will be your name on the `git blame`.
8. **Thou shalt not retry a payout blindly.** If EcoCash times out, the payout may have gone through.
   We look it up. We do not send the money again "to be safe". Sending money twice is not safe.
   It is the opposite of safe. It is generous, at the client's expense.
9. **Thou shalt not weaken a security check to make a test pass.** Fix the test, or fix your code.
   The check stays. The check always stays.
10. **Thou shalt not rename `GATEWAY_KEY_PEPPER`, rotate it, or "clean it up".** Doing so kills every
    merchant's API key at once. You will hear about it. Everyone will hear about it.

## Setting up

Do the `.env` section of the [README](README.md#setting-up-your-env-read-this-or-suffer).
All of it. In order. Then come back. We'll wait.

```bash
python -m venv venv
```

```bash
venv/Scripts/python.exe -m pip install -r requirements.txt
```

(On Mac/Linux it's `venv/bin/python`. You knew that. Probably.)

## How to make a change

1. **Branch.** Never commit straight to `main`. `main` is sacred ground. Wipe your feet.

   ```bash
   git checkout -b fix/what-you-are-fixing
   ```

2. **Write the test first** if you're touching anything with money in it. Offline tests live in
   `tests/` and use a fake EcoCash, so you need zero credentials and zero internet. No excuses.

3. **Run the tests.**

   ```bash
   venv/Scripts/python.exe -m pytest tests -q
   ```

   All green or it didn't happen.

4. **If you touched an EcoCash call**, also run the real sandbox check. It uses *your* `.env`, not
   anyone else's:

   ```bash
   venv/Scripts/python.exe sandbox_check.py
   ```

   Add `--charge` to test a real USSD prompt. Enter PIN `0000` when it buzzes. Not your real PIN.
   **Never your real PIN.** The sandbox will fail it, and also: why.

5. **Check you're not leaking anything** before every push:

   ```bash
   git diff --cached
   ```

   Read it. Actually read it. Look for phone numbers, references, keys, tokens, and that one
   `print()` you swore you'd remove.

6. **Push your branch and open a pull request.** Say what you changed and how you tested it.
   "fixed stuff" is not a description. It is a confession.

## If you leaked a secret

Don't panic. Okay, panic a little. Then:

1. **Tell the repo owner immediately.** Not tomorrow. Not after lunch. Now.
2. **Rotate the secret** (new EcoCash credentials from the portal, new keys from
   `python -m gateway.keygen`). Deleting the commit does **not** un-leak it. GitHub remembers.
   Bots remember. Bots never forget.
3. Then we fix the history together, calmly, and nobody brings it up at Christmas. Much.

## Where things live

| Path | What it is | How careful to be |
|---|---|---|
| `gateway/security.py` | Encryption, API keys, webhook signing, URL checks | Extremely. Pretend it's made of glass. |
| `gateway/routes_merchant.py` | Charges, refunds, payouts | Very. This is where money moves. |
| `gateway/providers.py` | Talks to EcoCash | Very. Read the EcoCash docs before you "improve" anything. |
| `gateway/routes_admin.py` | Merchant onboarding, keys | Very. It hands out the keys to the kingdom. |
| `tests/` | The thing that stops you from embarrassing yourself | Add to it freely. Delete from it never. |

Questions? Ask. Asking is free. Fixing a double payout at 2am is not.
