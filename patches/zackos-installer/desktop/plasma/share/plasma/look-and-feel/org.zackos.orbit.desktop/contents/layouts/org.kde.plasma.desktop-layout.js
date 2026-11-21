// Plasma evaluates this on first login or when the user applies the desktop layout.
const bar = new Panel();
bar.location = "top";
bar.height = Math.round(gridUnit * 2.25);
bar.floating = true;

const launcher = bar.addWidget("org.kde.plasma.kickoff");
launcher.currentConfigGroup = ["General"];
launcher.writeConfig("icon", "zackos-orbit");
launcher.writeConfig("menuLabel", "Apps");
launcher.writeConfig("favorites", ["org.kde.dolphin.desktop", "org.kde.konsole.desktop", "systemsettings.desktop"]);
bar.addWidget("org.kde.plasma.pager");
bar.addWidget("org.kde.plasma.panelspacer");

const clock = bar.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
clock.writeConfig("dateFormat", "custom");
clock.writeConfig("customDateFormat", "ddd d MMM");
bar.addWidget("org.kde.plasma.panelspacer");
bar.addWidget("org.kde.plasma.systemtray");
bar.addWidget("org.kde.plasma.showdesktop");

const dock = new Panel();
dock.location = "bottom";
dock.height = Math.round(gridUnit * 3);
dock.floating = true;
dock.alignment = "center";
dock.lengthMode = "fit";
dock.hiding = "dodgewindows";
const tasks = dock.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:org.kde.dolphin.desktop",
    "applications:org.kde.konsole.desktop",
    "applications:systemsettings.desktop"
]);

for (const desktop of desktopsForActivity(currentActivity())) {
    desktop.wallpaperPlugin = "org.kde.image";
}
