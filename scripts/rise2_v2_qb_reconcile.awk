# qB QSettings reconciliation: policy, private derived bootstrap, existing profile.
# Preserve unrelated lines exactly; never print a value in an error diagnostic.
function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
function fail() { bad=1; print "qB bootstrap: invalid or incomplete configuration" > "/dev/stderr"; exit 1 }
function emit(group, key) {
    for (key in desired) if (groups[key] == group) print names[key] "=" desired[key]
    emitted[group]=1
}
function compatibility(group, name, value, key) {
    key=group SUBSEP name
    desired[key]=value; groups[key]=group; names[key]=name; found[key]=1; compat[key]=1
}
function obsolete(group, key) {
    if (migration >= 2 || group != "Preferences") return 0
    return key == "Downloads\\SavePath" || key == "Connection\\ProxyPeerConnections" || key == "Connection\\ProxyOnlyForTorrents" || key == "Connection\\ProxyType" || index(key, "Connection\\Proxy\\") == 1
}
BEGIN { section="" }
FNR == 1 {
    section=""
    # qB upgrade.cpp migration <6 overwrites modern proxy profiles/hostname DNS.
    # Supply its legacy inputs without skipping unrelated upstream migrations.
    if (FILENAME == ARGV[2] && migration < 6) {
        compatibility("Network", "Proxy\\OnlyForTorrents", "true")
        compatibility("BitTorrent", "Session\\ProxyHostnameLookup", "true")
    }
}
{
    line=trim($0)
    if (line ~ /^\[.*\]$/) {
        section=substr(line, 2, length(line)-2)
        if (++sections[FILENAME SUBSEP section] > 1) fail()
        if (FILENAME == ARGV[3]) {
            print $0
            emit(section)
        }
        next
    }
    split_at=index(line, "=")
    key=trim(substr(line, 1, split_at-1))
    value=trim(substr(line, split_at+1))
    identity=section SUBSEP key
    if (line == "" || line ~ /^[#;]/) {
        if (FILENAME == ARGV[3]) print $0
        next
    }
    if (!split_at || !section || ++seen[FILENAME SUBSEP identity] > 1) fail()
    if (FILENAME == ARGV[1]) {
        desired[identity]=value; groups[identity]=section; names[identity]=key
    } else if (FILENAME == ARGV[2]) {
        if (identity in desired) {
            if (!(identity in compat) && value != desired[identity]) fail()
            found[identity]=1
        } else if (section == "Preferences" && key == "WebUI\\Username") {
            if (value !~ /^[A-Za-z0-9_.@-]+$/) fail()
            desired[identity]=value; groups[identity]=section; names[identity]=key; found[identity]=1
        } else if (section == "Preferences" && key == "WebUI\\Password_PBKDF2") {
            if (value !~ /^"@ByteArray\([A-Za-z0-9+\/=]+:[A-Za-z0-9+\/=]+\)"$/) fail()
            desired[identity]=value; groups[identity]=section; names[identity]=key; found[identity]=1
        }
    } else if (!(identity in desired) && !obsolete(section, key) && !(section == "Preferences" && (key == "WebUI\\Password" || key == "WebUI\\Password_ha1"))) {
        print $0
    }
}
END {
    if (bad) exit 1
    if (!("Preferences" SUBSEP "WebUI\\Username" in desired) || !("Preferences" SUBSEP "WebUI\\Password_PBKDF2" in desired)) fail()
    for (key in desired) if (!(key in found)) fail()
    for (key in desired) if (!(groups[key] in emitted)) {
        print "\n[" groups[key] "]"
        emit(groups[key])
    }
}
