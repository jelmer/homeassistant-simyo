"""Interactive app check; caches only completed sessions in an ignored file.

Requires aiohttp. Run from an interactive terminal; credentials/code are hidden.
No account settings are changed. MFA requests use Simyo's remember-device option.
"""

import asyncio
import getpass
import importlib.util
import sys
import types
from pathlib import Path

import aiohttp
from session_store import clear_session, load_session, save_session

SESSION_PATH = Path(__file__).resolve().parents[1] / "apk/.analysis/simyo-session.json"

package = types.ModuleType("simyo_live_check")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components/simyo")
]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "simyo_live_check.api", Path(package.__path__[0]) / "api.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


async def check(phone, password=None):
    async with aiohttp.ClientSession() as session:
        saved = load_session(SESSION_PATH, phone)
        client = api.SimyoClient(
            session,
            phone,
            "",
            device_id=saved["device_id"] if saved else None,
            session_token=saved["session_token"] if saved else None,
        )
        data = None
        try:
            if saved:
                print("Opgeslagen appsessie hergebruiken...")
                try:
                    data = await client.fetch_all()
                except api.SimyoAuthError:
                    clear_session(SESSION_PATH)
                    saved = None
                    print(
                        "Simyo heeft de opgeslagen sessie afgewezen; opnieuw aanmelden is nodig."
                    )
            if not saved:
                password = (
                    password
                    if password is not None
                    else getpass.getpass("Mijn Simyo-wachtwoord (verborgen): ")
                )
                client = api.SimyoClient(
                    session, phone, password, device_id=client.device_id
                )
                try:
                    await client.login()
                except api.SimyoMfaRequired:
                    print("Simyo vraagt om 2FA. Gebruik de zojuist ontvangen code.")
                    code = getpass.getpass("Verificatiecode (verborgen): ")
                    await client.verify_code(code.strip())
                    del code
                # Save before reading: parsing/HTTP failures must not lose MFA.
                save_session(SESSION_PATH, phone, client)
                print(
                    "Aangemelde appsessie lokaal opgeslagen; wachtwoord en code zijn niet opgeslagen."
                )
                data = await client.fetch_all()
            save_session(SESSION_PATH, phone, client)
            balance = data.get("balance", {}).get("result", {}).get("balance")
            if not isinstance(balance, (int, float)) or isinstance(balance, bool):
                raise api.SimyoApiError("prepaid_balance_value_missing")
            print("Ophalen van beltegoed en verbruik geslaagd.")
            # An empty password proves this token works without a hidden login.
            restored = api.SimyoClient(
                session,
                phone,
                "",
                device_id=client.device_id,
                session_token=client.session_token,
            )
            await restored.fetch_all()
            save_session(SESSION_PATH, phone, restored)
            print(
                "Hergebruik van de bevestigde appsessie geslaagd (zonder nieuwe 2FA)."
            )
            return 0
        except api.SimyoApiError as error:
            save_session(SESSION_PATH, phone, client)
            print(f"Aanvraag mislukt: {error.reason}; HTTP-status: {error.status}")
        except api.SimyoInvalidCode:
            print("Code geweigerd. Er is niet automatisch opnieuw geprobeerd.")
        except api.SimyoRateLimited:
            print("Te veel pogingen. Wacht voordat je het script opnieuw start.")
        except api.SimyoDataAuthError as error:
            clear_session(SESSION_PATH)
            print(
                f"Gegevenssessie geweigerd: {error.endpoint}; HTTP-status: {error.status}"
            )
        except api.SimyoAuthError:
            clear_session(SESSION_PATH)
            print("Inloggegevens geweigerd of verificatiesessie verlopen.")
        if SESSION_PATH.exists():
            print("De appsessie blijft bewaard voor een volgende poging.")
        return 1


if __name__ == "__main__":
    if not sys.stdin.isatty():
        raise SystemExit(
            "Start dit script in een interactieve terminal; pipe geen inloggegevens."
        )
    phone = input("Telefoonnummer (06xxxxxxxx): ").strip()
    try:
        raise SystemExit(asyncio.run(check(phone)))
    except KeyboardInterrupt:
        print("Geannuleerd.")
    except Exception as error:  # noqa: BLE001 -- redact any unexpected HTTP data
        # Do not print arbitrary exception text/tracebacks containing HTTP data.
        print(f"Onverwachte fout ({type(error).__name__}); details afgeschermd.")
