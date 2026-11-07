# Vinduesopmåling

En lille app til opmåling af vinduer og glas. Opmålingerne synkroniseres via Home Assistant.

- Bredde og højde i mm (målt midt på), antal ens, rum/placering og note
- Foto af vinduet, som appen laver om til en skitse med målene påskrevet
- Samlet antal vinduer og glas pr. opmåling, og listen kan deles på SMS eller mail
- Virker uden internet og synkroniserer, når der er forbindelse
- Data gemmes i Home Assistant under `/config/opmaaling/` (sager som JSON, billeder som JPEG)

## Installation

1. HACS → ⋮ → **Brugerdefinerede repositories** → `https://github.com/amigoogfutte/opmaaling`, type **Integration**
2. Installér **Vinduesopmåling**, og genstart Home Assistant
3. **Indstillinger → Enheder og tjenester → Tilføj integration → Vinduesopmåling**

## Brug

- **Computer:** Vælg **Opmåling** i Home Assistants sidemenu.
- **iPhone:** Åbn `https://<din-home-assistant>/opmaaling-app/index.html` i Safari, og vælg **Del → Føj til hjemmeskærm**. Første gang logger du ind med dit Home Assistant-login.

## API (kræver Home Assistant-token)

| Metode | Sti | |
|---|---|---|
| GET | `/api/opmaaling/sager` | Liste: id, updated, deleted |
| GET/PUT | `/api/opmaaling/sager/{id}` | Én opmåling (JSON) |
| GET | `/api/opmaaling/fotos` | Liste over billed-id'er |
| GET/PUT | `/api/opmaaling/fotos/{id}` | Billede (JPEG) |
