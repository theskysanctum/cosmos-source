#!/usr/bin/env python3
"""Real, hand-written platform history blurbs for the Platforms page's
right-hand info pane (see app.py's build_platforms_page()) - per direct
instruction, paraphrased in this project's own words rather than lifted
verbatim from Wikipedia or any other source. Facts (release years,
notable titles, market outcomes) are drawn from general, widely-known
gaming history, not copied text - each entry is an original 2-3 sentence
summary written for this project specifically.

Deliberately not every systems.json platform is covered - the 11 "Game
Engines" entries (RPG Maker, WOLF RPG, GameMaker, Love2D, Ren'Py, TIC-80,
AGS) are development tools, not historical hardware/software platforms
with a release story to tell, so they're left out rather than forcing an
awkward blurb - the info pane just omits the description section for
those, same "missing just means missing" rule this file's own YEAR/
HISTORY lookups already follow."""

YEAR = {
	"nes": 1985, "snes": 1990, "n64": 1996, "gc": 2001,
	"ps1": 1994, "ps2": 2000,
	"mastersystem": 1985, "genesis": 1988, "segacd": 1991, "saturn": 1994, "dreamcast": 1998,
	"atari2600": 1977, "atari7800": 1986,
	"pcengine": 1987,
	"colecovision": 1982, "intellivision": 1979, "vectrex": 1982,
	"gb": 1989, "gba": 2001, "nds": 2004, "psp": 2004,
	"gamegear": 1990, "lynx": 1989, "ngp": 1998, "wonderswan": 1999,
	"model2": 1993, "atomiswave": 2003,
	"c64": 1982, "amiga": 1985, "atarist": 1985, "zxspectrum": 1982, "msx": 1983,
	"apple2": 1977,
}

HISTORY = {
	"nes": "Nintendo's Famicom launched in Japan in 1983 and reached the West as the Nintendo Entertainment System in 1985, pulling the US home console market back from its post-1983 collapse. Its cartridge library introduced now-iconic franchises like Super Mario Bros. and The Legend of Zelda.",
	"snes": "Released in 1990, the Super Nintendo Entertainment System pushed 16-bit graphics and sound further than most rivals, thanks to custom expansion chips built into individual cartridges. It remains one of the most fondly remembered libraries in gaming, from Chrono Trigger to Super Metroid.",
	"n64": "Launched in 1996, the Nintendo 64 was among the last major consoles to ship on cartridges rather than optical discs, trading storage space for near-instant load times. Its analog stick and rumble pak reshaped how 3D games were controlled going forward.",
	"gc": "The GameCube arrived in 2001 as Nintendo's compact, disc-based follow-up to the N64, notable for its miniDVD format and distinctive cube shape. It's remembered for strong first-party titles and a controller layout that still echoes through Nintendo hardware today.",
	"ps1": "Sony's first PlayStation launched in 1994 and helped move the industry from cartridges to CDs, with cheaper storage enabling full-motion video and CD-quality audio. It became the first console to sell over 100 million units, built on genre staples like Final Fantasy VII and Metal Gear Solid.",
	"ps2": "Released in 2000, the PlayStation 2 remains the best-selling console of all time, helped along by backward compatibility and a built-in DVD player that made it a common home theater fixture. Its massive library spans everything from Grand Theft Auto to Shadow of the Colossus.",
	"mastersystem": "Sega's Master System launched in 1985 as its answer to the NES, finding only modest US success but becoming a genuine market leader in Brazil, where Tec Toy kept producing new hardware and games for decades afterward.",
	"genesis": "Known as the Mega Drive outside North America, Sega's 16-bit console launched in 1988 and built its identity around speed and attitude, headlined by Sonic the Hedgehog. Its \"blast processing\" marketing became one of the era's defining console-war slogans.",
	"segacd": "The Sega CD, released in 1991, was an add-on that gave the Genesis a CD-ROM drive years before the format was standard, enabling full-motion video games and enhanced ports. It was expensive and its library uneven, but it previewed where the industry was heading.",
	"saturn": "Sega's Saturn launched in 1994 with a complex dual-CPU design that made ambitious 3D games difficult to program, even though it excelled at 2D. It found a stronger following in Japan than the West, especially for its shooter and fighting game library.",
	"dreamcast": "The Dreamcast, released in 1998, was Sega's final console and its most forward-looking - the first with a built-in modem for online play. Critically beloved but commercially short-lived, it was discontinued in 2001 as Sega exited the hardware business.",
	"atari2600": "Released in 1977, the Atari 2600 popularized the idea of a home console that played interchangeable cartridges rather than fixed built-in games. It dominated the early home gaming market before the 1983 crash it also helped cause through market oversaturation.",
	"atari7800": "The Atari 7800 was designed in 1984 but, due to Atari's post-crash turmoil, didn't launch until 1986. It was backward-compatible with 2600 cartridges and offered noticeably sharper graphics, though it arrived well behind the NES in the market.",
	"pcengine": "NEC's PC Engine (sold as the TurboGrafx-16 in the US) launched in Japan in 1987 as a diminutive console with graphics chips ahead of its time. Its extensive CD-ROM add-on library made it especially significant in Japan despite a rough go in the American market.",
	"colecovision": "ColecoVision launched in 1982 with graphics that rivaled arcade machines of the era and a library leaning heavily on arcade ports like Donkey Kong. It was a strong early performer before getting caught up in the 1983 market crash.",
	"intellivision": "Mattel's Intellivision arrived in 1979 as a direct rival to the Atari 2600, distinguished by a numeric keypad controller and a sports-and-strategy-heavy library. Its \"intelligent television\" branding emphasized more cerebral gameplay than Atari's arcade action.",
	"vectrex": "Released in 1982, the Vectrex was unique in shipping with its own built-in vector-graphics monitor rather than connecting to a TV, giving its games a crisp, glowing line-drawn look. It was discontinued just a year later after the 1983 crash.",
	"gb": "Launched in 1989, the Game Boy prioritized battery life and durability over graphical power, and that trade-off paid off - it dominated handheld gaming for over a decade, cemented by Tetris as its pack-in game.",
	"gba": "The Game Boy Advance, released in 2001, brought SNES-caliber 2D graphics to a handheld for the first time, backed by an enormous library spanning original games and ports of older console classics.",
	"nds": "Nintendo's DS launched in 2004 with dual screens and touch input, an unconventional bet that paid off as it became one of the best-selling handhelds ever, reaching well beyond the traditional gaming audience with titles like Brain Age and Nintendogs.",
	"psp": "Sony's PlayStation Portable launched in 2004 as a genuine console-caliber handheld, playing games from UMD discs alongside movies and music. It built a strong library of console-quality portable titles, including major PlayStation franchise entries made specifically for it.",
	"gamegear": "Sega's Game Gear, released in 1990, countered the Game Boy with a full-color backlit screen at the cost of much shorter battery life. Its library leaned on ports of Genesis and Master System games rather than many exclusives.",
	"lynx": "Atari's Lynx launched in 1989 as the first handheld with a color backlit screen, a year ahead of Sega's Game Gear. Its bulky size and short battery life kept it from unseating the Game Boy despite the technical lead.",
	"ngp": "SNK's Neo Geo Pocket (and its improved Color revision) launched in the late 1990s with a tight, responsive analog thumbstick that stood out among handhelds of its time. Its library leaned on fighting games and SNK's own arcade franchises.",
	"wonderswan": "Bandai's WonderSwan, released in Japan in 1999 with input from Game Boy creator Gunpei Yokoi, never launched outside Japan but built a loyal library there, including entries in the Final Fantasy and Kingdom Hearts series.",
	"mame": "MAME - the Multiple Arcade Machine Emulator - is a long-running open-source project that emulates the original hardware of thousands of arcade boards rather than any single console, preserving games that would otherwise only exist in unmaintained cabinets.",
	"fbneo": "FinalBurn Neo is a sister project to MAME focused on efficient, accurate emulation of arcade boards and a handful of consoles like the Neo Geo, favored on lower-powered hardware for its lighter footprint.",
	"model2": "Sega's Model 2 arcade board, introduced in 1993, was among the first widely deployed systems capable of real-time 3D polygon graphics, powering games like Virtua Fighter 2 and Daytona USA.",
	"atomiswave": "Sammy's Atomiswave, introduced in 2003, was an arcade board built on Dreamcast-derived architecture, making it comparatively simple to port games between the two systems.",
	"teknoparrot": "TeknoParrot is a modern frontend for running a wide range of PC-based arcade games - many originally shipped on Windows-based arcade cabinets - outside of their original cabinet hardware.",
	"vpinball": "Visual Pinball is a physics-based simulator for pinball tables, supporting both faithful recreations of real machines and original fan-made tables built with its own editor.",
	"pc": "The PC has been a gaming platform since well before dedicated consoles existed, and remains the most open one - hardware, storefronts, and mods are largely unrestricted compared to closed console ecosystems.",
	"dos": "MS-DOS was the dominant PC operating system through the 1980s and into the mid-1990s, and its text-mode command line underpinned an enormous era of PC gaming before Windows took over, from Doom to the early Sierra adventure games.",
	"c64": "The Commodore 64, released in 1982, went on to become the best-selling single computer model in history, thanks to an aggressive price and a vast software library spanning both games and serious productivity software.",
	"amiga": "Commodore's Amiga line, starting in 1985, offered graphics and sound capabilities well ahead of contemporary PCs, making it a favorite for both games and early desktop video production.",
	"atarist": "Released in 1985, the Atari ST competed directly with the Amiga and found particular favor with musicians thanks to built-in MIDI ports, alongside a solid library of games.",
	"zxspectrum": "Sinclair's ZX Spectrum, launched in 1982, became one of the UK's most influential home computers, spawning a distinctive bedroom-coder games industry and rubber-keyed hardware still fondly remembered today.",
	"msx": "MSX was a standardized home computer architecture launched in 1983 and licensed to multiple manufacturers, mainly finding success in Japan, parts of Europe, and South America rather than the US.",
	"apple2": "Introduced in 1977, the Apple II was one of the first mass-produced personal computers and helped establish the home computer market, with an open architecture that encouraged an enormous range of third-party software and games.",
}
