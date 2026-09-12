# Bedienung im Feld

## Der Bildschirm

![Die Kabine](bilder/kabine.png)

Die Anzeige ist der Blick über die Haube: das Feld liegt in Perspektive vor dem
Traktor, die Spur läuft zum Horizont. Alles Wichtige liegt als Glasfläche
darüber.

```
 ┌──────────────────────────────────────────────────────────────┐
 │ Oberes Feld     ▓▓▓▓▓░░░░░░░│░░░░░░░░░░       RTK fix ±1 cm │
 │ Kontur · 6,00 m        ◀ 12 cm                 Master       │
 │                                                       + − ⛰ ☼│
 │                    ╲    │    ╱                               │
 │                     ╲   │   ╱             ◀ 10 cm   10 cm ▶  │
 │   8,6  km/h          ╲  │  ╱             A setzen  A+  Kontur│
 │   5    0,09  −3,5°    ╲ │ ╱              Wende   Kurve   Menü│
 │   Ring ha    Hang      ▲                ● MARKIEREN  an      │
 │   [1][2][3][4]   bis Vorgewende 38 m   ● LENKUNG    aktiv    │
 └──────────────────────────────────────────────────────────────┘
```

**Der Lichtbalken oben ist die eine Anzeige, die man im Augenwinkel lesen kann.**
Er leuchtet auf der Seite, auf der die Spur liegt – **zum Licht hin lenken**, bis
nur noch die Mitte brennt. Eine Lampe sind 5 cm. Grün heißt unter 5 cm, gelb bis
20 cm, rot darüber. Die Zahl darunter sagt es genau; der Pfeil daneben zeigt in
dieselbe Richtung: wohin zu lenken ist.

**Links unten** die Fahrt: Geschwindigkeit, Spur- oder Ringnummer, bearbeitete
Hektar, Hang mit Ausgleich. Ist eine Applikationskarte gewählt, steht der
**Sollwert** daneben – die Zahl, nach der der Streuer läuft; wo keine Karte
gilt, steht *ohne Karte*. Die kleine Kurve darunter ist der Verlauf der
Abweichung der letzten Sekunden – man sieht, ob die Führung ruhig arbeitet oder
pendelt. Bei mehreren Teilbreiten steht der Sektionsbalken darunter.

**Unten Mitte** zwei Balken übereinander, je nachdem, was gerade gilt:

* Der **Plan**: welche Bahn von wie vielen läuft und wie viel Hektar noch offen
  sind. Er erscheint, sobald ein Arbeitsplan für das Feld gerechnet ist.
* Die Strecke **bis zum Vorgewende** – bis zum *Beginn* des Vorgewendes, nicht
  bis zur Grenze. Sie erscheint nur, wenn eine Feldgrenze gespeichert und eine
  Vorgewendetiefe eingestellt ist. Unter 20 m wird sie bernsteinfarben und ruft
  es über der Karte aus. Während einer Wende zeigt dieselbe Fläche den
  Fortschritt der Wende.

**Rechts** die Handlungsspalte, nach Wichtigkeit von unten nach oben – unten
liegt die Hand am montierten Tablet ohnehin:

| | |
|---|---|
| **Lenkung** | der größte Knopf. Grün und leuchtend: die Automatik lenkt. Bernstein: scharf, aber gerade nicht am Lenken – der Grund steht darunter (zu langsam, kein RTK, zu weit von der Spur). Grau mit rotem Punkt: aus. Tippen schaltet um. |
| **Markieren** | darüber. An = die bearbeitete Fläche wird gemalt, Hektar und Überlappung laufen mit, die Fahrt wird aufgezeichnet. Aus = nur Führung. Die Bezeichnung der Arbeit (Grubbern, Säen…) steht unter Menü → Aufträge. |
| **A setzen / B setzen · A+ · Kontur** | eine Spur anlegen – siehe unten. Kontur ist zugleich die Grenze: einmal um das Feld fahren, fertig. |
| **◀ 10 cm · 10 cm ▶** | das ganze Spurmuster um zehn Zentimeter versetzen. |
| **Wende · Kurve · Menü** | klein, weil selten gebraucht. |

**Auf der Karte** liegt, wenn ein Arbeitsplan gerechnet ist, seine Einteilung:
blasse blaue Linien mit Bahnnummern, dazu die gepunkteten Ringe des
Vorgewendes. Gefahrene Bahnen werden grün, die gerade geführte kräftig blau und
dicker. Die Nummern erscheinen erst, wenn weit genug hineingezoomt ist – bei
achtzig Bahnen auf Feldübersicht lägen achtzig Zahlen übereinander.

**Oben rechts** unter den Statuschips: Näher, Weiter, Ansicht, Tag/Nacht. Die
Ansicht wechselt zwischen **Perspektive** (Blick über die Haube), **Flach**
(Fahrtrichtung oben, wie eine Karte) und **Norden oben**. **Tag** schaltet auf
hellen Grund – ein schwarzer Bildschirm hat in praller Sonne die wenigsten
Reserven. Beides wird auf dem Tablet gemerkt.

Zwei Finger auf der Karte zoomen. Die Perspektive ist keine 3D-Grafik: die
Karte wird flach gezeichnet und vom Browser gekippt, das kostet das Tablet fast
nichts.

**Der Hang-Wert** erscheint nur, wenn ein Neigungssensor eingerichtet ist. Er
zeigt die Schräglage, und der Ausgleich dazu läuft im Hintergrund: bei 3 m
Antennenhöhe sind 6° Hang 31 cm, um die die Spur sonst wandern würde. Der Wert
färbt sich, sobald der Ausgleich mehr als 15 cm ausmacht – dann arbeitet er
gerade spürbar.

## Ein Feld anlegen und vermessen

1. Auf das Feld fahren, **Menü → Felder**, Namen eingeben, *Feld hier anlegen*.
   Der Bezugspunkt wird an der aktuellen Position gesetzt und ändert sich nie
   wieder – daran hängt, dass zwei Traktoren dieselbe Fläche gleich sehen.
2. **⬠ Kontur** drücken und einmal um das Feld fahren (die Taste zeigt
   währenddessen „Kontur fertig“).
3. Am Ausgangspunkt wieder **⬠ Kontur** drücken. Die Fläche in Hektar steht
   sofort da und ist gespeichert – und die Kontur ist aktiv: Ring 0 ist die eben
   gefahrene Grenze, jeder weitere Ring liegt eine Arbeitsbreite weiter innen.
   Grenze und Kontur sind dieselbe Linie, deshalb ein Knopf.

Soll die Grenze später neu abgefahren werden: **Menü → Felder → Grenze neu
abfahren**, dann um das Feld fahren und mit **Kontur** abschließen.

Die Grenze ist nicht nur Buchhaltung: die Sektionen schalten außerhalb der
Grenze automatisch ab.

### Felder aus dem Flächenantrag (Shapefile)

Wer die Flächen schon als Shapefile hat – aus dem Flächenantrag, aus dem
Kataster, aus einem anderen Lenksystem –, muss sie nicht abfahren:
**Menü → Felder → Shapefile einlesen** und die drei Dateien `.shp`, `.dbf`
und `.prj` zusammen auswählen (auf dem Tablet aus dem Dateimanager, vom
USB-Stick oder aus dem Download-Ordner). Je Fläche entsteht ein Feld; der Name
kommt aus der Tabelle (`SCHLAGNAME`, `NAME`, `FLIK` …), die Hektarzahl aus
der Grenze, der Bezugspunkt liegt mitten im Feld.

Verstanden werden ETRS89/UTM (Zone 32N und 33N, mit oder ohne die Zone im
Ostwert) und WGS84-Grad – das ist, was die Antragsportale und QGIS ausgeben.
Gauß-Krüger auf dem alten DHDN-Datum wird abgelehnt, mit Begründung: die
Umrechnung dahinter ist ohne Tabellen nur auf Meter genau, und eine Grenze,
die still zwei Meter daneben liegt, ist schlimmer als keine. Dann in QGIS oder
im Portal als EPSG:25832 exportieren.

Kommt dieselbe Datei später noch einmal – der Antrag hat sich geändert –,
bekommt ein Feld gleichen Namens die neue Grenze, seine Spuren und Arbeiten
bleiben. Löcher in einer Fläche (ein Teich) werden weggelassen; eine Fläche
aus mehreren Teilen wird zu mehreren Feldern „Name (1)", „Name (2)".

## Eine Spur anlegen

**Gerade Spuren (AB-Linie)** – der Normalfall:

1. Am Feldrand in Arbeitsrichtung ausrichten, **A setzen** drücken.
2. Bis zum anderen Ende fahren – derselbe Knopf heißt jetzt **B setzen**, drücken.

Fertig. Alle weiteren Spuren liegen im Abstand der Arbeitsbreite parallel dazu.

**A+ – wenn kein Platz für einen B-Punkt ist:** die Taste **A+** rechts. Die
Spur läuft dann genau in die
Richtung, in die der Traktor gerade zeigt. Gut, um die Richtung vom Nachbarfeld
oder vom letzten Jahr zu übernehmen.

**Kurven** – für krumme Felder: **∿ Kurve** drücken, die gewünschte Linie
abfahren, wieder **∿ Kurve** drücken. Alle weiteren Spuren folgen dieser Form.

**Kontur – die Feldgrenze selbst als Spur:** die Taste **Kontur** rechts.
Gibt es noch keine Grenze, zeichnet der Druck sie auf (siehe oben); gibt es
sie, wird sie zur Spur. Ring 0 ist die Feldgrenze, jeder weitere Ring liegt
eine Arbeitsbreite weiter innen. Kein A- und kein B-Punkt nötig, und nichts
abzufahren, was ohnehin schon abgefahren wurde – die Grenze ist da. Gedacht für das Vorgewende und für krumme
Schläge. Wie herum die Grenze aufgezeichnet wurde, spielt keine Rolle: Ring 1
liegt immer weiter innen, nie weiter außen.

**Ecken.** Eine abgefahrene Grenze hat den Wendekreis des Traktors, eine aus
dem Flächenantrag hat rechte Winkel. Um eine rechtwinklige Ecke kommt die
Lenkautomatik nicht: sie hält die Spur bis zur Ecke, dahinter wäre sie mehr
als die erlaubte Abweichung daneben und setzt aus. Deshalb steht 40 m vorher
oben links „Ecke in 38 m – von Hand um die Ecke, danach greift die Lenkung
wieder“: um die Ecke lenkt der Fahrer, auf der nächsten Kante zieht die
Automatik von selbst wieder auf den Ring, sie bleibt scharf.

Die Kontur wird **nicht gespeichert**: sie entsteht bei jedem Aufruf neu aus der
aktuellen Grenze. Wer die Grenze neu abfährt, hat sofort die neue Kontur. Eine
gespeicherte Kopie läge nach dem nächsten Abfahren still daneben – und man
sähe es erst abends an den Streifen im Feld.

Angelegte Spuren stehen unter **Menü → Spuren** und lassen sich jederzeit wieder
laden. Die Kontur steht nicht in dieser Liste; sie wird über den Knopf geholt.

Jede Spur gehört zu ihrem Feld. Unter **Menü → Felder → Spuren ▾** stehen sie
beim Feld, mit *Laden*, *Umbenennen* und *Löschen* – und mit dem Knopf, der
eine Spur zur Saisonspur macht.

### Saisonspur und Fahrgassen

Beim Säen entscheidet sich, wo das ganze Jahr gefahren wird: die Fahrgassen.
Düngen und Spritzen sollen später genau diese Spuren treffen, sonst liegt die
Spritze neben der Fahrgasse und fährt durch den Bestand. Deshalb wird die
Spur vom Säen als **Saisonspur** gespeichert: **Menü → Felder → Spuren ▾ →
Als Saisonspur**, Fahrgassenabstand in Metern eingeben (bei 6 m Sämaschine
und 24 m Spritze: 24). Von da an

* wird diese Spur beim Laden des Feldes **automatisch aktiv** – dieses Jahr,
  bei jeder Arbeit, ohne dass jemand daran denken muss;
* ist jede vierte Spur (24 m / 6 m) auf dem Bildschirm **rot gestrichelt** –
  das sind die Fahrgassen. Die aktive Spur leuchtet rot statt grün, wenn sie
  eine Fahrgasse ist;
* steht im Spur-Chip oben links „Fahrgassen 24 m".

Wird mit der Spritze gearbeitet (Maschine mit 24 m wählen), ist der Spurabstand
24 m und jede Spur eine Fahrgasse – die Führung läuft auf denselben Linien.

Im nächsten Jahr zählt die Saisonspur nicht mehr; beim Laden kommt dann wieder
die zuletzt benutzte Spur, bis die neue Saat ihre Saisonspur bekommt. Alte
Saisonspuren bleiben mit Jahreszahl in der Liste stehen. Fahrgassenabstand 0
nimmt einer Spur die Saison wieder weg.

## Arbeiten

![Aufträge](bilder/auftraege.png)

**Markieren** drücken. Die Bezeichnung (Grubbern, Säen, Spritzen) steht unter
**Menü → Aufträge** und bleibt, bis eine andere eingetragen wird. Ab jetzt:

* wird die bearbeitete Fläche grün mitgezeichnet,
* laufen Hektar, Strecke und Überlappung mit,
* wird die Fahrspur für den Nachweis aufgezeichnet.

Am Ende wieder **Markieren** drücken. Der Auftrag steht unter **Menü → Aufträge** mit
Datum, Dauer, Strecke, Fläche und doppelt bearbeiteter Fläche – als GPX,
GeoJSON oder CSV herunterladbar. *Alle Arbeiten als CSV* gibt die Liste für das
Büro.

## Spurversatz (Nudge)

**Rechts heißt rechts vom Sitz aus** – auch auf einer Spur, die gerade
rückwärts (von B nach A) gefahren wird, und auf der Kontur, egal ob im oder
gegen den Uhrzeigersinn. Nach **10 cm ▶** leuchtet der Balken rechts, der Pfeil
zeigt nach rechts, und die Lenkung zieht nach rechts auf die versetzte Spur.


Die Tasten **◀ 10 cm** und **10 cm ▶** verschieben das **ganze Spurmuster** um
zehn Zentimeter – das ist der Schritt, den man im Feld braucht. Dafür gibt es zwei gute Gründe:

* Zwei Traktoren stehen minimal verschieden auf derselben Spur.
* Nach einer Pause hat sich die RTK-Lösung um ein paar Zentimeter verschoben.

Wichtig: der Versatz verschiebt alle Spuren gleichzeitig, nicht nur die
aktuelle – sonst wäre das Muster nach einer Runde krumm. Er wird beim Feld
gespeichert.

## Sektionen

Bei mehreren Teilbreiten zeigt der Balken unten, welche Sektion offen ist.
Grün = an, grau = automatisch zu, rot = vom Fahrer zugeschaltet.

Automatisch geschlossen wird eine Sektion, wenn sie über bereits bearbeitetes
Land oder über die Feldgrenze hinaus laufen würde. Geprüft wird ein Stück
voraus – je schneller gefahren wird, desto weiter, damit ein Ventil rechtzeitig
schließt.

Antippen schaltet eine Sektion von Hand ab und wieder frei. Die Automatik
insgesamt lässt sich unter **Menü → Maschine** abschalten.

## Arbeitsplan (Menü → Plan)

Eine Spur setzt man beim Fahren: A drücken, B drücken, los. Das ist richtig für
den ersten Schlag. Wer ein Feld zum dritten Mal bearbeitet, weiß aber vorher,
wie es am wenigsten Wenden kostet – und genau das rechnet der Arbeitsplan aus
der Feldgrenze aus.

**Voraussetzung ist die Feldgrenze.** Ohne sie gibt es keinen Plan, und das
steht dann auch so da. Einmal umfahren (siehe *Ein Feld anlegen und vermessen*)
oder ein Shapefile einlesen.

**Rechnen:** Menü → Plan → *Plan rechnen*. Was im Formular leer bleibt, kommt
aus der Maschine und aus den Vorgewende-Einstellungen – Arbeitsbreite,
Vorgewendetiefe und Wendekreis stehen dort schon. Zweimal dasselbe einzutippen
ist die zuverlässigste Art, zwei verschiedene Werte zu bekommen.

| Feld | Wofür |
|---|---|
| **Arbeitsbreite** | Der Abstand der Bahnen. Vorbelegt mit dem Spurabstand der Maschine. |
| **Vorgewende** | Tiefe in Arbeitsbreiten, wie im Reiter Vorgewende. |
| **Wenderadius** | Der kleinste Wendekreis. Er entscheidet, ob die Reihenfolge „im Sprung" sinnvoll ist. |
| **Geschwindigkeit** | Nur für die Zeitschätzung. |
| **Richtung** | Leer lassen heißt: das Programm sucht sie selbst. Eine eingetragene Zahl wird nicht überstimmt. |
| **Reihenfolge** | *fortlaufend* fährt Bahn für Bahn. *Im Sprung* lässt so viele Spuren aus, wie der Wendekreis braucht, und holt sie danach nach. |

**Was dabei herauskommt**, steht als Zeile über der Liste: Anzahl der Bahnen,
die gewählte Richtung (und ob sie selbst gesucht wurde), die Zahl der Wenden,
die Gesamtstrecke und eine geschätzte Dauer. Darunter die Flächen – was die
Bahnen abdecken, was aufs Vorgewende entfällt, und die Feldfläche daneben.

**Die Richtung** sucht das Programm, indem es ausprobiert: jede Kante der
Feldgrenze ist ein Kandidat, dazu ein Raster über alle Richtungen und eine
feine Nachsuche um den Sieger. Gewonnen hat, wer mit den wenigsten Bahnen
auskommt; bei Gleichstand die kürzere Gesamtstrecke. Auf einem rechteckigen
Schlag kommt genau die lange Seite heraus.

**Im Feld sichtbar** wird der Plan als blasse blaue Linien mit Nummern, dazu
die gepunkteten Ringe des Vorgewendes. Gefahrene Bahnen werden grün, die gerade
geführte kräftig blau und dicker. Unten steht, welche Bahn von wie vielen läuft
und wie viel Hektar noch offen sind.

**Eine Bahn fahren:** in der Liste auf *fahren* tippen, oder *Nächste Bahn
fahren*. Daraus wird eine ganz normale AB-Spur zwischen den beiden Enden der
Bahn – Lichtbalken, Abweichung, Lenkung und Wende arbeiten wie sonst auch. Der
Spurabstand kommt dabei aus dem Plan, nicht aus dem Maschinenprofil: sonst
lägen die angezeigten Nachbarspuren neben den geplanten Bahnen.

„Nächste Bahn" ist die **nächstgelegene** offene, nicht die nächste in der
Liste. Wer die Reihenfolge einmal verlassen hat, soll nicht ans andere Feldende
geschickt werden, nur weil dort eine Lücke blieb.

**Was als gefahren gilt**, liest der Plan aus der bearbeiteten Fläche, nicht
daraus, was angetippt wurde: eine Bahn ist durch, wenn neun Zehntel ihrer Länge
markiert sind. An den Enden fehlen fast immer ein paar Meter, weil die Wende
früher beginnt als die Bahn endet. Was ohne **Markieren** gefahren wurde, zählt
nicht – das Programm sieht nur, was es gemalt hat.

**Der Plan gehört zum Feld** und wird mit abgeglichen. Der zweite Traktor
bekommt beim Laden des Feldes denselben Plan mit denselben Bahnnummern – auch
dann, wenn jemand inzwischen die Grenze nachgemessen hat. Neu gerechnet ergäbe
eine andere Einteilung, und dann meinen zwei Fahrer mit „Bahn 12"
verschiedene Stellen im Feld. Wer wirklich neu einteilen will, drückt *Plan
rechnen* noch einmal.

## Applikationskarten (Menü → Plan)

Eine Applikationskarte sagt für jeden Punkt des Feldes, wie viel dort
ausgebracht werden soll – 140 kg/ha auf dem guten Boden, 90 auf der Kuppe. Sie
kommt vom Berater, aus dem Satellitenbild oder aus der Ertragskarte des
Vorjahres.

**Einlesen:** Menü → Plan → *Karte einlesen*. Drei Wege, erkannt an den
Dateiendungen:

* **Shapefile** mit einer Wertespalte: `.shp`, `.dbf` und möglichst `.prj`
  zusammen auswählen. Enthält die Tabelle mehrere Zahlenspalten, wird gefragt,
  welche der Sollwert ist – geraten wird hier nicht.
* **GeoJSON**: eine Datei, die Eigenschaft mit der Zahl wird gesucht.
* **ISO-XML** aus dem Terminal: `TASKDATA.XML` **zusammen mit** der Rasterdatei
  (`.BIN`) auswählen. Ohne die Rasterdatei ist die Karte leer, und das sagt der
  Import auch, statt eine halbe Karte anzulegen.

**Die Einheit ist der gefährliche Teil.** Im ISO-XML steht nicht die Menge,
sondern eine ganze Zahl und eine Kennung, die sagt, wie sie gemeint ist. Ein
Faktor 100 daneben, und der Streuer legt das Hundertfache ab. Deshalb zeigt der
Import die kleinste, größte und mittlere Menge an und warnt, wenn sie außerhalb
dessen liegt, was eine Ausbringmenge üblicherweise ist. Abgelehnt wird nichts –
die Karte kann recht haben –, aber der Fahrer sieht es, bevor der Streuer
läuft. Ist die Kennung nicht hinterlegt, bleiben die Zahlen unverändert stehen
und sagen es. Über **Einheit** lässt sich die Umrechnung ausdrücklich vorgeben.

**Im Feld** steht der Sollwert groß bei den Fahrtzahlen. Nachgeschlagen wird er
an der Stelle des **Geräts**, nicht an der Antenne: bei einem gezogenen Gerät
liegen dazwischen in der Kurve mehrere Meter, genug, um an der Zonengrenze die
falsche Menge zu nehmen. Wo keine Karte gilt, steht **ohne Karte** – das ist
ausdrücklich etwas anderes als null.

**Was ausgebracht wurde**, wird beim Fahren mitgeschrieben: jede neu markierte
Stelle wird auf den Sollwert gebucht, der dort galt. Unter der Karte stehen die
geplanten Mengen je Zone, die ausgebrachte Menge und die Abweichung in Prozent.
*Ausbringung als CSV* gibt das für die Schlagkartei aus. Dieselben Zahlen
hängen an der Arbeit und stehen in der Sammel-CSV unter Menü → Aufträge in
derselben Zeile wie Fläche und Strecke – eine zweite Datei daneben geht im
Büro verloren.

Diese Zahl ist ehrlich beschränkt, und sie sagt das auch von sich: als
Grundlage steht **Sollwert der Karte** dabei. Das System weiß, wo die Maschine
gefahren ist und was die Karte dort verlangt hat. Ob der Streuer die Menge auch
wirklich abgelegt hat, weiß es nur, wenn die Maschine es zurückmeldet – dann
steht dort **Istwert der Maschine**.

## Vorgewende und Wenden (Menü → Vorgewende)

Das Vorgewende ist kein zweites Feld, sondern eine Tiefe: so viele
Arbeitsbreiten vom Rand nach innen, wie zum Wenden gebraucht werden. Es kommt
deshalb aus der Feldgrenze und wandert mit dem Gerät mit – wer ein breiteres
Gerät anhängt, hat automatisch ein tieferes Vorgewende.

**Einstellen:** Anzahl der Vorgewendespuren (üblich 2), Alarmabstand,
kleinster Wendekreis der Maschine, Wendemuster und Wenderichtung. Eine Breite
je Spur trägt nur ein, wer ein Vorgewende will, das breiter ist als die
Maschine; sonst bleibt dort die Null stehen.

**Im Feld sichtbar** wird daraus dreierlei:

* Die **gestrichelte Linie** innerhalb der Feldgrenze – dort endet die Arbeit.
* Die Anzeige **„m bis Vorgewende"** oben. Sie zählt bis zum *Beginn* des
  Vorgewendes, nicht bis zur Grenze. Wer bis zur Grenze zählt, wendet zu spät.
  Die Zahl wird negativ, sobald man drin steht – das sagt auch, wie weit.
* Der **Annäherungsalarm**: unter dem eingestellten Abstand blinkt der Hinweis
  rot und der Vorgewendering wird rot.

**Arbeitsreihenfolge – Vorgewende zuerst oder zuletzt.** Das Programm erzwingt
keine Reihenfolge; es kann nicht wissen, warum heute anders herum gefahren wird.
Aber es sieht hin: ob das Vorgewende schon bearbeitet ist, liest es aus der
markierten Fläche (abgetastet entlang der Mitte des Vorgewendes, alle drei
Meter). Steht „zuerst" und eine AB-Spur ist aktiv, während das Vorgewende noch
leer ist, sagt es das oben links – mit dem Prozentsatz, der schon bearbeitet
ist. Auf der Kontur zeigt es, welcher Vorgewende-Ring gerade dran ist. Was nicht
mit **Markieren** gefahren wurde, zählt dabei nicht – das Programm sieht nur,
was es gemalt hat.

### Wenden

Die kleine Taste **Wende** rechts hat drei Zustände, und sie sagt jedes Mal,
was sie als Nächstes tut:

1. **„Wende"** – planen. Die Route erscheint gestrichelt auf der Karte, blau
   wenn sie im Feld liegt, rot wenn nicht.
2. **„Ω los"** – die Maschine folgt der Route.
3. **„Ω Stopp"** – zurück an den Fahrer.

Zwei Druck, nicht einer: eine Wende, die auf Knopfdruck losfährt, hat niemand
vorher angesehen.

**Die beiden Muster:**

| | |
|---|---|
| **Ω-Wende** (weiter Bogen) | Holt nach vorn aus und kommt in einem Bogen zurück. Braucht mehr Vorgewendetiefe, fährt dafür weichere Radien. |
| **U-Wende** (kompakt) | Zwei Viertelkreise mit einer Geraden dazwischen. Kommt mit deutlich weniger Tiefe aus; dafür wächst die Zwischengerade mit dem Spurversatz. |

**Was die Wende nicht tut:** rückwärts fahren. Gesteuert wird das Lenkrad, nicht
Fahrstufe und nicht Gas – ein Muster mit Rückwärtsgang wäre eine Route, die die
Maschine nicht fahren kann. Wo der Platz für keines der beiden Muster reicht,
wird von Hand gewendet.

**Die Sicherheitsprüfung** verlangt, dass *jeder* Punkt der Route innerhalb der
Feldgrenze liegt. Ein einziger draußen genügt zur Ablehnung – der Rest im Feld
hilft nichts, wenn das Vorderrad im Graben steht. Ohne gespeicherte Feldgrenze
kann nicht geprüft werden; dann sagt die Anzeige das auch.

Während der Wende zeigt die Abweichung oben den Abstand **zur Wenderoute**,
nicht zur verlassenen Spur. Das ist kein Schönheitsfehler, sondern nötig: die
Lenkautomatik gibt ab, wenn die Abweichung zu groß wird, und in einer Wende ist
man von der alten Spur zwangsläufig weit weg. Wer der Route nicht folgt, bekommt
sie abgebrochen und lenkt selbst weiter.

Bei einem Positionsausfall endet die Wende sofort. Blind auf einem Bogen zu
lenken ist schlimmer als gar nicht zu lenken.

## Lenkautomatik

Nur verfügbar, wenn sie bei der Installation freigegeben wurde.

**Lenkung** drücken schaltet scharf. Sie lenkt erst, wenn alles stimmt:

| Bedingung | Anzeige, wenn sie fehlt |
|---|---|
| in der Konfiguration freigegeben | „in der Konfiguration deaktiviert" |
| vom Fahrer scharf geschaltet | „nicht scharf" |
| Spur geladen | „keine Spur aktiv" |
| RTK-Fix vorhanden | „RTK nötig, aktuell: …" |
| schnell genug | „zu langsam" |
| nicht zu schnell | „zu schnell" |
| näher als 1,5 m an der Spur | „zu weit von der Spur (… m)" |
| Positionsdaten frisch | „GPS-Daten veraltet (… s)" |

Sitzt ein Drehgeber am Lenkmotor, lernt das System beim Scharfschalten die
Geradeausstellung – **beim Scharfschalten müssen die Räder also gerade stehen.**
Neu setzen lässt sie sich über **Menü → System → Lenkung: Mitte lernen**, etwa
nachdem von Hand nachgelenkt wurde.

**Ins Lenkrad greifen schaltet sofort ab** und die Lenkung bleibt aus, bis sie
neu scharf geschaltet wird. Am Vorgewende wird von Hand gewendet – eine
automatische Wende gibt es bewusst nicht.

## Mit zwei Traktoren auf einem Feld

Beide laden dasselbe Feld (**Menü → Felder → Laden**). Jeder zeichnet seine
eigene Fläche auf; sobald Verbindung zum Master besteht, sieht jeder auch, was
der andere schon bearbeitet hat – und die Sektionen schalten entsprechend ab.

Ohne Verbindung arbeitet jeder Traktor vollständig weiter. Der Abgleich holt
alles nach, sobald der Master wieder erreichbar ist.

## Neigungssensor nullen

Einmal beim Einbau und danach, wenn der Sensor bewegt wurde: auf **ebenem**
Boden **Menü → System → Neigungssensor nullen**. Der Sensor sitzt nie exakt
waagerecht in der Kabine, und ein Grad Montagefehler sind bei 3 m Antennenhöhe
schon 5 cm Dauerversatz in jeder Spur.

Prüfen lässt sich der Ausgleich am besten so: über eine Furche fahren, sodass
der Traktor kippelt. Die angezeigte Abweichung darf dabei fast ruhig bleiben.
Wird sie beim Kippeln größer, arbeitet der Ausgleich verkehrt herum – dann
gehört in die Konfiguration `roll_sign: -1.0`.

## Mehrere Maschinen (Menü → Maschine)

Der Schlepper mit dem Grubber ist eine andere Maschine als derselbe Schlepper
mit der Spritze: andere Arbeitsbreite, anderes Gerät hinter der Achse, gezogen
oder angebaut. Deshalb gibt es oben im Reiter eine **Auswahl aller Maschinen**.
*Neue Maschine* legt eine weitere an – mit den aktuellen Werten als Vorlage,
unter neuem Namen; danach nur ändern, was anders ist. Die Auswahl gilt sofort:
Spurabstand, Sektionen und Werkzeugpunkt springen um, die Spur bleibt. Die
gewählte Maschine steht auch im Chip oben rechts und in jeder aufgezeichneten
Arbeit. Die letzte Maschine lässt sich nicht löschen – eine muss es geben.

**Gerät hinter Achse, gezogen oder angebaut.** Diese Maße bestimmen, wo
markiert wird – die grüne Fläche entsteht am Gerät, beim gezogenen Gerät mit
seinem Nachlauf in der Kurve. **Gelenkt wird immer auf die Hinterachse**, nie auf
das Gerät: ein Punkt fünf Meter hinter der Achse schwenkt beim Einlenken erst
zur falschen Seite, und eine Führung, die ihn auf die Spur zwingen will,
schaukelt sich auf (im Simulator auf ±3,5 m, mit der Lenkung im Sekundentakt
an und aus). Auf gerader Spur läuft das Gerät ohnehin in der Achsspur; ein
seitlicher Versatz des Geräts wird als Versatz der Achse mitgenommen.

## Abstimmung bei Tempo, Latenz und Teilbreiten (Menü → Maschine)

Drei Werte, die aus dem Cerea-Handbuch übernommen sind und erst zählen, wenn die
Grundlage stimmt – Antennenmaße gemessen, Vorzeichen geprüft, RTK-Fix dauerhaft.

**Absenkung ab (km/h) und Restfaktor.** Dieselbe Einstellung, die im Schritttempo
sauber nachführt, schaukelt bei zwölf km/h auf: der Fehler wird schneller
eingefahren, als die Lenkung ihn abbauen kann. Ab der Schwelle wird die Lenkung
linear weicher, bei doppelter Schwelle bleibt der Restfaktor stehen und fällt
nicht weiter – sonst stünde die Lenkung bei hohem Tempo praktisch still.
0 schaltet die Absenkung aus. Richtwert zum Anfangen: Schwelle bei der
Geschwindigkeit, ab der es unruhig wird, Restfaktor 0,5.

**Aktor-/GNSS-Latenz (ms).** Zwischen der Position vom Empfänger und der
wirklichen Radbewegung liegt Zeit: Empfänger, Programm, Platine, Motor,
Lenkgestänge. Ohne Ausgleich lenkt das System immer auf die Stelle, an der die
Maschine vor einem Augenblick war – in schnell gefahrenen Kurven läuft sie
deshalb hinterher. Geführt wird dann auf den Punkt, an dem sie sein *wird*;
markiert wird weiterhin dort, wo sie wirklich war. Bei 240 ms und 3 m/s sind das
gut 70 cm Vorhalt. Voreinstellung 0, in 50-ms-Schritten erhöhen und im Stand
gegenprüfen.

**Teilbreiten: Abschalten ab.** Ein offenes Teilstück schließt erst, wenn dieser
Anteil seiner Breite überdeckt ist (Voreinstellung 0,9). Vorher entschied ein
einzelner Punkt in der Mitte – dann flackert die Teilbreite am Fahrspurrand,
sobald der Traktor ein paar Zentimeter pendelt. Auf neuem Boden geht sie sofort
wieder auf.

Die **Feldgrenze** bleibt davon unberührt: was hinausragt, schaltet ab,
unabhängig von der Überdeckung. Draußen zu arbeiten wäre kein Schönheitsfehler.

**Gezogenes Gerät (Anhängerkinematik).** Ein angebautes Gerät dreht sich mit dem
Traktor. Ein gezogenes nicht: es hängt an der Deichsel und schwenkt erst ein,
während es gezogen wird. In der Kurve steht es deshalb spürbar *innerhalb* der
Fahrspur – ein starrer Versatz behauptet das Gegenteil und malt die bearbeitete
Fläche an die falsche Stelle.

Mit der Option wird die Fläche dort markiert, wo das Gerät wirklich ist. Die
**Deichsellänge** ist der Abstand vom Zugpunkt bis zur Geräteachse; je kürzer
sie ist, desto schneller folgt das Gerät. Im Stand schwenkt nichts, egal wie
weit am Lenkrad gedreht wird – auch das steckt im Modell und entspricht der
Erfahrung.

Geführt wird weiterhin das **Fahrzeug**. Auf ein nachlaufendes Gerät zu führen
klingt genauer, macht die Lenkung aber unruhig: das Gerät zieht erst dorthin,
wo das Fahrzeug vorhin war.

## Angeschlossene Geräte finden (Menü → System)

Nach dem Einbau, nach dem Umstecken, nach dem Tausch eines Kabels: **Menü →
System → Geräte suchen**. Gesucht werden

* der **Empfänger** an den seriellen Anschlüssen – ein u-blox-Gerät (F9P) wird
  als solches erkannt;
* die **Lenksteuerung** (Phidget) mit Seriennummer und Kanälen – Motor,
  Radwinkelsensor oder Drehgeber;
* der **Neigungssensor** (Tinkerforge IMU Brick oder Bricklet) über den Brick
  Daemon.

Was gefunden wird, steht in der Liste; daraus wird ein Vorschlag für die
Einstellungen gebaut (Quelle, Anschluss, Seriennummer, Rückmeldung, UID).
**Gefundene Geräte übernehmen** schreibt ihn in die Konfigurationsdatei –
derselbe Weg wie beim Tippen unter *Einstellungen*, mit derselben Prüfung.
Empfänger und Sensor werden sofort neu verbunden; nur die Lenksteuerung braucht
einen Neustart, das sagt die Meldung. Die Suche ändert nichts von selbst und
dauert einige Sekunden (Phidget und Brick Daemon wollen warten). Fehlt ein
Treiber oder läuft der Brick Daemon nicht, steht das als Satz in der Liste,
nicht als Fehlercode.

## Aufträge je Feld (Menü → Aufträge)

Die aufgezeichneten Arbeiten stehen **nach Feld gruppiert**: je Feld eine
Überschrift mit Anzahl, Hektar und Stunden, darunter die einzelnen Arbeiten mit
GPX/GeoJSON/CSV. Die Bezeichnung der nächsten Arbeit (Grubbern, Säen, Spritzen)
wird oben eingetragen und gemerkt; gestartet wird mit **Markieren** in der
Kabine.

## Änderungen einspielen (Menü → System → Aktualisierung)

Ein neuer Stand kommt in drei Schritten auf das Tablet, ohne Terminal und ohne
Installationsskript:

1. **Auf dem PC** das Paket bauen: `python scripts/make_update.py` (oder
   `-o D:\stick`, direkt auf den Stick). Heraus kommt eine Zip-Datei
   `agripilot-update-<Version>-<Stand>.zip`, knapp ein Megabyte, mit
   Prüfwerten für jede Datei.
2. **Auf dem Tablet** die Datei hinbringen (USB-Stick, WLAN-Freigabe,
   Messenger – egal), dann **Menü → System → Aktualisierung → Update-Paket
   einspielen** und die Datei wählen. Das Programm prüft das Paket (Prüfwerte,
   nur die vier Programmordner, nichts außerhalb), tauscht `backend`,
   `frontend`, `scripts` und `docs` aus und legt den alten Stand als Sicherung
   ab. Konfiguration, Datenbank und Python-Umgebung bleiben, wie sie sind.
   Gibt es neue Bibliotheken, versucht es `pip` – ohne Netz scheitert das mit
   Ansage, die Programmdateien sind trotzdem neu.
3. **Neu starten** drücken. Die Lenkung geht aus, eine laufende Arbeit wird
   gesichert, das Programm beendet sich und kommt von selbst wieder (unter
   systemd über den Dienst, sonst als eigener Nachfolger); die Anzeige verbindet
   sich in ein paar Sekunden neu und lädt die Seite frisch.

Läuft das Programm aus einem Git-Klon und hat das Tablet gerade Netz, geht es
kürzer: **Aus GitHub holen** (`git pull`, nur vorspulen), dann Neu starten.

**Wenn der neue Stand nicht taugt:** **Vorigen Stand zurückholen** – die jüngste
Sicherung wird wieder eingesetzt (der jetzige Stand wandert dabei selbst in eine
Sicherung), dann Neu starten. Drei Sicherungen werden aufbewahrt, ältere
verschwinden von selbst.

Oben im Abschnitt steht, was gerade läuft: Version, Stand (Commit), wann
eingespielt, und aus welchem Ordner.

## Rohdaten aufzeichnen und abspielen (Menü → System)

Ein Fehler auf dem Feld ist teuer zu untersuchen: er passiert einmal, bei Regen,
mit einem Anhänger hinten dran – und wenn man ihn nachstellen will, steht der
Traktor schon wieder in der Halle. Was bleibt, ist die Erinnerung des Fahrers,
und die reicht nicht, um zwischen „der Empfänger hat gesprungen", „der Sensor
hatte ein falsches Vorzeichen" und „die Führung hat sich verrechnet" zu
unterscheiden.

**Aufzeichnen:** Menü → System → *Aufzeichnung starten*. Ab da wird
mitgeschrieben, was **hereinkommt** – die rohen NMEA-Sätze und die Lagemeldungen
des Neigungssensors, jeweils mit Zeitstempel. Läuft eine Aufzeichnung, zählt die
Anzeige daneben mit. Rund 7 MB je Stunde; bei 200 MB endet sie von selbst,
damit eine vergessene Aufzeichnung dem Pi nicht die Karte vollschreibt.

Sinnvoll ist, sie **vor** der Fahrt zu starten, bei der man etwas vermutet.
Nachträglich lässt sich nichts aufzeichnen.

**Abspielen:** in der Liste bei der Aufzeichnung auf *Abspielen*. Sofort, ohne
Neustart, läuft dieselbe Fahrt noch einmal – durch dieselbe Rechenkette, mit
denselben Zeitabständen. Position und Neigung kommen aus derselben Datei und
bleiben deshalb im selben Takt. Die Lenkung geht dabei aus und eine laufende
Aufzeichnung wird beendet: einer Maschine, die gerade lenkt, darf keine andere
Positionsquelle untergeschoben werden. Zurück auf den Empfänger geht es über
Einstellungen → Empfänger → Quelle – ebenfalls sofort. Das gilt für alle
Einstellungen an Empfänger und Sensor: Anschluss, Baudrate, Adresse wirken ohne
Neustart, die Verbindung wird neu aufgebaut.

**Was aufgezeichnet wird, ist das Rohe, nicht das Errechnete.** Ein Protokoll
der Ergebnisse würde jeden Auswertungsfehler mit aufzeichnen, den man gerade
sucht: liegt er in der Auswertung, steht in der Datei schon das falsche Ergebnis,
und der Abspielmodus bestätigt ihn brav.

Es ist eine Textdatei – im Notfall genügt ein Texteditor:

```
# agripilot-rohdaten 1
# begonnen 2026-09-10T22:43:14
0.028	N	$GPGGA,204314.028,4808.232142,N,01134.535997,E,4,22,0.6,520.0,M,45.0,M,,*50
5.012	I	0.579,0.000,48.331,17.901,3
```

Erste Spalte: Sekunden seit dem Beginn. Zweite: `N` für einen NMEA-Satz, `I` für
eine Lagemeldung (Neigung, Nicken, Kurs, Drehrate, Kalibrierung).

Zwei Dinge, die nicht gehen und aus gutem Grund nicht gehen: während des
Abspielens lässt sich **nicht** aufzeichnen – eine Kopie der Kopie wäre keine
neue Messung. Und eine fehlende Aufzeichnungsdatei fällt **nicht** still auf den
Simulator zurück; dann stünde eine erfundene Fahrt auf dem Bildschirm, während
man glaubt, die eigene zu sehen. Stattdessen sagt die Statuszeile, dass die
Datei fehlt.

## Einbau-Checkliste (Menü → Einbau)

Für die Inbetriebnahme, nicht für den Alltag. Die acht Schritte in der
Reihenfolge, in der sie sich gegenseitig absichern; abgehakt wird von oben nach
unten, der nächste offene Schritt ist blau umrandet.

Grün heißt: **das Programm hat es gemessen**. Grau heißt: das Programm kann es
nicht wissen, hier steht deine Aussage dafür gerade – etwa dass der Not-Aus in
der Motorleitung sitzt. Rot heißt: die Anlage widerspricht gerade, und dann
lässt sich der Schritt auch nicht abhaken.

Schritte ohne Bestätigungstext haben keinen Knopf: sie erledigen sich selbst,
sobald die Messung trägt.

*Zurücksetzen* löscht alle Bestätigungen – sinnvoll nach einem Umbau, sonst nie.

## Kleine Regeln, die viel sparen

* **Vor dem ersten Zug prüfen, ob „RTK fix" steht.** Mit „RTK float" wandert die
  Spur über den Tag um Dezimeter, und man sieht es erst an den Streifen im Feld.
* **Arbeitsbreite vor dem Start eintragen.** Sie legt den Spurabstand fest;
  später geändert, passt die schon bearbeitete Fläche nicht mehr dazu.
* **Bei jedem Gerätewechsel die Maße prüfen.** Anbaugeräte haben verschiedene
  Abstände zur Hinterachse.
* **Die Überlappung ehrlich einstellen.** Wer 10 cm Überlappung will, trägt sie
  ein, statt eng zu fahren – dann stimmen auch die Hektarzahlen.
