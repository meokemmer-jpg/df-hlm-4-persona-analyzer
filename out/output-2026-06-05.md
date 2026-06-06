# df-hlm-4-persona-analyzer — Output [CRUX-MK]
*Autonom aktiviert 2026-06-05T14:11:06.934833+00:00 | ollama-local/qwen2.5:14b-instruct*

# Dokumentation: DF-HLM-4 Persona-Cohort-Analyse für HeyLou Marketing-Welle
Marketing-Welle 2

## Überblick
DF-HLM-4 ist eine statistische Analysten-Komponente, die speziell für die z
zweite Marketingwelle von HeyLou entwickelt wurde. Ihre Hauptfunktion beste
besteht darin, kohortenbasierte Analysen durchzuführen und daraus wertvolle
wertvolle Erkenntnisse für das Marketing-Team zu extrahieren. Dieser Dark F
Factory arbeitet standardmäßig im Mock-Modus, der eine bestimmt synthetisch
synthetische Buchungskohorte generiert.

## Einstieg
Um die Funktion von DF-HLM-4 vollständig nutzen zu können, ist es notwendig
notwendig, einige Umgebungsvariablen (ENV-Vars) zu setzen. Diese Variablen 
definieren den Betriebsmodus und bestimmen das Verhalten der Komponente in 
verschiedenen Szenarien.

### ENV-Vars
- **DF_HLM_4_REAL_PMS_ENABLED**: Dieser Parameter aktiviert die Echtzeit-Da
Echtzeit-Datenquelle, wenn auf "true" gesetzt.
- **PHRONESIS_TICKET_PATTERN**: Gibt den Muster für das Phronesis-Ticket an
an, welches die Berechtigung zur Nutzung der realen PMS-API bestätigt.
- **Q_0_APPROVAL**: Gilt für spezielle genehmigungsbedarfene Aktionen.

## Analyse-Personas
DF-HLM-4 unterstützt eine Vielzahl von Personifikationen, welche auf die ve
verschiedenen Zielgruppen des HeyLou-Marketingplans zugeschnitten sind. Hie
Hier ist eine Auflistung der unterstützten Persona-Typen:

- Bayer-Werks
- Bosch-Travel
- Familie
- Buchmesse-Verleger
- KIT-Forscher
- Salzburg-Klassik
- Wedding
- Mittelalter

## Analyseparameter
Für die durch DF-HLM-4 durchgeführten Analysen werden mehrere Parameter def
definiert, welche die Tiefe und Genauigkeit der Ergebnisse beeinflussen:

- **kmeans_k**: Dieser Wert bestimmt die Anzahl der Clustermittelwerte für 
k-means-Klustering.
- **feature_vector**: Diese Liste von Merkmalsvektoren gibt an, welche Date
Datenpunkte für die Analysen relevant sind:
  - avg_stay_nights (Durchschnittliche Übernachtungsanzahl)
  - channel (Buchungskanal)
  - price_segment (Preissegmentierung der Zimmerkategorien)
  - season_index (Saisonindex zur Klassifizierung von Ferien- und Geschäfts
Geschäftsreisen)
  - group_size (Gruppengröße)

## Betriebsparameter
DF-HLM-4 kann in verschiedenen Modi betrieben werden, um die Flexibilität i
im Umgang mit Datenquellen zu gewährleisten. Dazu gehören Modusvorkommnisse
Modusvorkommnisse wie vollständiger Betrieb, API-unreachable-Betrieb und Be
Betrieb auf der Basis von vorliegenden Konsolidierungscaches.

### Ausgabepfade
- **output_path**: Pfad zur Veröffentlichungsergebnisse.
- **report_dir**: Ordner für Berichte.
- **audit_log_path**: Pfad zu den Audit-Logs.
- **state_dir**: Speicherort der internen Zustände und Schnittstellenstatus
Schnittstellenstatus.

## Zusammenfassung
DF-HLM-4 ist ein wichtiger Bestandteil des Marketingplans von HeyLou, da si
sie es ermöglicht, kohortenbasierte Analysen durchzuführen, um optimierte M
Maßnahmen für die Zielgruppen zu entwickeln. Durch den Einsatz realer und s
synthetischer Daten kann sie wertvolle Erkenntnisse liefern und eine fundie
fundierte Entscheidungsgrundlage bieten.

Diese Dokumentation bietet einen Überblick über den Betrieb der DF-HLM-4, i
ihre unterstützten Personifikationen und die Parameter, welche für die Anal
Analyse relevant sind.