"""Interactive live check of this fork; never prints or saves auth values.

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


async def check(phone, password):
    async with aiohttp.ClientSession() as session:
        client = api.SimyoClient(session, phone, password)
        try:
            try:
                await client.login()
            except api.SimyoMfaRequired:
                print("Simyo vraagt om 2FA. Gebruik de zojuist ontvangen code.")
                code = getpass.getpass("Verificatiecode (verborgen): ")
                await client.verify_code(code.strip())
                del code
            print("Aanmelden geslaagd; bevestigde sessie ontvangen.")
            data = await client.fetch_all()
            balance = data.get("balance", {}).get("result", {}).get("balance")
            if not isinstance(balance, (int, float)) or isinstance(balance, bool):
                raise api.SimyoApiError("prepaid_balance_value_missing")
            print("Ophalen van beltegoed en verbruik geslaagd.")
            restored = api.SimyoClient(
                session,
                phone,
                password,
                session_token=client.session_token,
                web_session=client.web_session,
            )
            await restored.fetch_all()
            print(
                "Hergebruik van de bevestigde websitesessie geslaagd (zonder nieuwe 2FA)."
            )
        except api.SimyoApiError as error:
            print(f"Aanvraag mislukt: {error.reason}; HTTP-status: {error.status}")
        except api.SimyoInvalidCode:
            print("Code geweigerd. Er is niet automatisch opnieuw geprobeerd.")
        except api.SimyoRateLimited:
            print("Te veel pogingen. Wacht voordat je het script opnieuw start.")
        except api.SimyoDataAuthError as error:
            print(
                f"Inloggen/2FA geslaagd, maar gegevenssessie geweigerd: {error.endpoint}; HTTP-status: {error.status}"
            )
        except api.SimyoMfaRequired:
            print(
                "Gegevens ophalen vraagt onverwacht opnieuw om 2FA. Er is geen nieuwe code ingediend."
            )
        except api.SimyoAuthError:
            print("Inloggegevens geweigerd of verificatiesessie verlopen.")


if __name__ == "__main__":
    if not sys.stdin.isatty():
        raise SystemExit(
            "Start dit script in een interactieve terminal; pipe geen inloggegevens."
        )
    phone = input("Telefoonnummer (06xxxxxxxx): ").strip()
    password = getpass.getpass("Mijn Simyo-wachtwoord (verborgen): ")
    try:
        asyncio.run(check(phone, password))
    except KeyboardInterrupt:
        print("Geannuleerd.")
    except Exception as error:  # noqa: BLE001 -- redact any unexpected HTTP data
        # Do not print arbitrary exception text/tracebacks containing HTTP data.
        print(f"Onverwachte fout ({type(error).__name__}); details afgeschermd.")
