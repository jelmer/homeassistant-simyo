# Simyo-integratie voor Home Assistant

Een aangepaste Home Assistant-integratie die de app-API van
[Mijn Simyo](https://mijn.simyo.nl/) periodiek uitleest en prepaidbeltegoed en
beschikbare verbruiksgegevens als sensoren beschikbaar maakt.

## Schermafbeeldingen

### Inloggen bij Mijn Simyo

Vul je telefoonnummer en Mijn Simyo-wachtwoord in Home Assistant in.

![Simyo-inlogformulier in Home Assistant](images/simyo-login.png)

### Tweestapsverificatie

Als je account tweestapsverificatie gebruikt, vul je de code in die Simyo je stuurt.

![Formulier voor Simyo-tweestapsverificatie](images/simyo-two-factor-verification.png)

### Prepaidbeltegoed in Mijn Simyo

Het prepaidoverzicht op de Simyo-website toont het beschikbare beltegoed.

![Prepaidbeltegoed op de Mijn Simyo-website](images/simyo-prepaid-balance-website.png)

### Sensoren in Home Assistant

De integratie toont het prepaidbeltegoed in Home Assistant. Andere sensoren
kunnen `Unknown` (onbekend) tonen wanneer de gegevens ontbreken of nog niet
correct aan een sensor zijn gekoppeld.

![Simyo-apparaat en beltegoedsensor in Home Assistant](images/simyo-home-assistant-sensors.png)

## App-route vanaf 0.3.0

De integratie gebruikt nu de Android-app-API, op basis van statisch onderzoek
van Mijn Simyo 4.31.2. De websiteproxy en websitecookies zijn verwijderd.

- Basis: `https://appapi.simyo.nl/webapi/api`.
- Aanmelden: `POST /v1/sessions` met telefoonnummer en wachtwoord.
- Verificatie: `POST /v2/security.verifyOTP` met de code en `rememberMe: true`,
  met het beperkte aanmeldtoken in `X-Session-Token`.
- Gegevens: `GET /v1/overview/prepaid` en `POST /v2/usage.aggregated`.
- Appheaders identificeren Android 4.31.2. Het openbare client-ID uit de app
  is een app-identificatie, geen accountwachtwoord.

Het apparaat-ID blijft gelijk bij herladen, herstarten en heraanmelden.
Alleen een volledig bevestigde appsessie wordt opgeslagen. Eenmalige codes
of beperkte MFA-tokens worden nooit opgeslagen. Eventuele vervangende
sessietokens uit gegevensantwoorden worden bewaard. Ruwe API-antwoorden
worden niet als sensorattributen gepubliceerd.

Bij een geweigerd token probeert de integratie eenmaal opnieuw aan te melden
met dezelfde apparaatidentiteit en de opgeslagen inloggegevens. Mislukt dit
of vraagt Simyo opnieuw MFA, dan blijft automatisch aanmelden geblokkeerd,
ook na een herstart, tot een succesvolle handmatige aanmelding. De eerste
herstelpoging kan dus een sms veroorzaken. Simyo bepaalt de geldigheidsduur;
een onbeperkte sessie wordt niet gegarandeerd.

### Overstappen vanaf een websiteversie

Bestaande entiteiten en hun unieke ID's blijven behouden. Bij de eerste start
worden oude websitecookies en het bijbehorende token niet als app-credentials
gebruikt. De bestaande apparaatidentiteit blijft behouden. De integratie maakt
een nieuwe appsessie en vraagt zo nodig eenmalig om verificatie. Daarna wordt
het app-token bij herstarts hergebruikt.

### Controle

De endpoints, headers en modellen zijn in de APK aangetroffen. Een lege
live aanvraag naar het app-aanmeldendpoint gaf de verwachte validatiefout
`missing phoneNumber`. Dit controleert de bereikbaarheid en het verzoekpad,
maar vervangt geen succesvolle aanmelding met MFA en gegevensopvraging.

De lokale regressietests controleren de echte HTTP-verzoeken op een lokale
server, OTP-isolatie, foutafhandeling, hergebruik van een app-token, een stabiel
apparaat-ID, tokenvervanging en begrensd herstel. De app-route is op 5 november 2026 ook met een echt account getest:
aanmelding met sms-verificatie, beltegoed en verbruik ophalen en hergebruik
van dezelfde appsessie in een nieuwe client zijn geslaagd. Langdurige werking
en herstarts in Home Assistant moeten afzonderlijk worden bevestigd. Eerdere succesvolle websitepraktijktests gelden niet als
praktijktest van deze nieuwe route.

```sh
python -m pip install aiohttp voluptuous
python -m unittest discover -s tests -v
python tools/verify_2fa.py
```

De interactieve praktijktest vraagt het wachtwoord en de code verborgen,
haalt gegevens op en maakt daarna een nieuwe client met hetzelfde app-token
en apparaat-ID. Een volledig aangemelde appsessie wordt bewaard in
`apk/.analysis/simyo-session.json` (uitgesloten van Git), ook als uitlezen
mislukt. Alleen een account-hash, apparaat-ID en sessietoken worden opgeslagen;
geen wachtwoord of code. Houd dit bestand privé. Een volgende test probeert
eerst deze sessie, zonder nieuw wachtwoord of sms-code. Logboeken tonen alleen vaste lokale foutcategorieën en statussen.

Dit is een onofficiële integratie voor een niet-openbare interface.
Onbekende verbruiksvelden blijven onbekend; er worden geen eenheden gegokt.

## Installatie

1. Kopieer `custom_components/simyo` naar de map `config/custom_components/` van Home Assistant.
2. Herstart Home Assistant.
3. Ga naar **Instellingen â†’ Apparaten & diensten â†’ Integratie toevoegen** en kies **Simyo**.
4. Vul je Simyo-telefoonnummer (bijvoorbeeld `0612345678`) en Mijn Simyo-wachtwoord in.
5. Vul zo nodig de nieuwste zescijferige verificatiecode in het aparte formulier in.

## Sensoren

| Sensor | Gegevenssectie |
| ------ | -------------- |
| Beltegoed | `balance` |
| Dataverbruik / resterende data | `usage` |
| Verbruikte / resterende belminuten | `usage` |
| Verbruikte / resterende sms'jes | `usage` |
| Kosten buiten de bundel | `usage` |

Sensoren publiceren alleen hun gekoppelde meetwaarde; het oude attribuut
`raw` is verwijderd om accountgegevens en tokens buiten sensorattributen te houden.

## Vernieuwen van gegevens

De integratie haalt elke 5 uur gegevens op. Simyo stuurt wijzigingen niet
rechtstreeks naar de integratie.

## Diagnose van de app-aanmelding

`python tools/probe_login.py` onderzoekt de rechtstreekse
app-aanmelding en toont de antwoordstructuur met afgeschermde waarden.
Dit hulpmiddel voert de sms-verificatie niet uit. Gebruik
`tools/verify_2fa.py` om de huidige aanmelding van deze fork te testen.

## Lokale logo's

Het officiÃ«le Simyo-pictogram en woordmerk staan in
`custom_components/simyo/brand/`, als PNG-bestanden in normale en hoge
resolutie, inclusief varianten voor het donkere thema. Home Assistant
2026.3 en hoger ondersteunt deze lokale afbeeldingen. De gebruikersinterface
kan ze daardoor tonen zonder afhankelijk te zijn van een externe logodienst.

Afbeeldingsbronnen: [Simyo-pictogram](https://www.simyo.nl/assets/favicon/apple-touch-icon.png)
en [Simyo-woordmerk](https://www.simyo.nl/assets/images/logo/simyo-yorange.svg).
De Simyo-naam en afbeeldingen blijven handelsmerken van hun respectieve
eigenaar. Deze onofficiÃ«le integratie is niet verbonden aan Simyo.
