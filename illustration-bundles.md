# Legacy illustration bundle index

**\* This index is being retired. Bundles are now shared through [avianvisitors.com/bundles](https://avianvisitors.com/bundles). The old community links are retained below during migration.**

Do not add new bundles here. See the [README](README.md#3-illustration-bundles) for the new bundle workflow, and follow the tracked [migration checklist](docs/bundle-catalog-migration.md) before removing this file.

The main repo ships a Western-US set reviewed by hand. Other people have generated sets for their own regions and keep them in their forks. These links are retained during the catalog migration.

## Using a set from this list

Prefer the catalog: if your region is published there, `sudo avian-bundle use '<BUNDLE_ID>'` installs a reviewed copy. Until a set below reaches the catalog, you can merge it into your own station's illustrations over SSH. The art is fetched at install time and never committed, so check each repo's license before you use or share it. The station does not review these sets; only fetch sources you trust.

```bash
~/BirdNET-Pi/avian/scripts/import_bundle.sh https://github.com/lloydalexporter/AvianAssets_GB-ENG
```

The script accepts a git URL, a GitHub `/tree/<branch>` URL, a `.zip` URL, or a local zip or folder. It finds the folder of `<genus>-<species>.png` and `<genus>-<species>-2.png` files, validates every PNG, and merges them through the same live-art transaction as `cutout.py`. Then it runs `build_masks.py`, which rebuilds `dims.json` and `masks.json` and refreshes the station's art revision so browsers reload. Don't edit `apt.js` on the station.

- Birds that already have art are kept; add `--replace` to swap them (originals are backed up).
- Pre-cut RGBA PNGs need no cutout. PNGs without transparency are skipped unless you add `--cutout`, which needs several GB of free RAM (see `cutout.py`).
- Run the same command again to pick up the set's updates. `--list` shows imported sets and `--remove <name>` undoes one.
- Imported art appears while the station uses its own illustrations. If you selected a downloaded catalog bundle, switch back in **Settings → Bird bundle**.
- New stations can do this during setup: add `--illustrations <URL>` to the installer.

## North America

- **Western US**: ships in the main repo
- **Florida, US (US-FL)**: [SupraBitKid's fork](https://github.com/SupraBitKid/AvianVisitors), 574 added species

## Europe

- **Switzerland (CH-BE)**: [theskyisthelimit's fork](https://github.com/theskyisthelimit/AvianVisitors/tree/europe-illustrations), 293 species
- **Germany**: [bassrelic's fork](https://github.com/bassrelic/AvianVisitors-German-Fork/tree/add_german_species), 37 species
- **The Iberian Peninsula**: [RoqueAlonso's fork](https://github.com/RoqueAlonso/AvianVisitors/tree/iberia-illustrations), 440 species. Work in progress: covers Madrid and Castilla-La Mancha (ES-MD, ES-CM) in full, with partial coverage elsewhere in the Peninsula.
- **Derbyshire, UK**: [jonnywright's repo](https://github.com/jonnywright/AvianAssets), not a full fork but just the illustrations, `dims.json` and `masks.json` for 302 species from Derbyshire, UK (GB-ENG-DBY).
- **England, UK**: [lloydalexporter's repo](https://github.com/lloydalexporter/AvianAssets_GB-ENG), just the illustration assets for 724 species.
- **Netherlands (NL-GR)**: [peterdeboer-nl's fork](https://github.com/peterdeboer-nl/AvianVisitors/tree/avian-visitors), 572 species

## Oceania

- **Victoria, Australia (AU-VIC)**: [TheWillni's repo](https://github.com/TheWillni/AusVicVisitors), not a full fork but just the illustrations, `dims.json` and `masks.json` for 398 species from Victoria, Australia (AU-VIC).
- **Canberra / ACT, Australia (AU-ACT)**: [opurtell's repo](https://github.com/opurtell/AvianVisitorsACT), the `avian/` overlay only, for 403 species. The AU-VIC bundle above plus the 5 species it was missing (Australasian Pipit, Cattle Egret, Variegated Fairywren, Intermediate Egret, Barn Owl). Covers everything BirdNET attempts at Canberra with `DATA_MODEL_VERSION=2`. Also ships the species derivation and a ready-made `include_species_list.txt` for the region.
