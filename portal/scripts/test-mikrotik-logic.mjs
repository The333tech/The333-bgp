import assert from "node:assert/strict";
import {
  RESERVED_PREFIXES,
  buildMikroTikCommands,
  isAsn,
  isIpv4,
  isStandardCommunity,
  parseRouterFacts,
  versionAtLeast,
} from "../src/components/mikrotikAssistantLogic.ts";

const empty = parseRouterFacts("");
assert.equal(empty.analyzed, false);
assert.equal(empty.version, null);

const routerOs6 = parseRouterFacts(`
version: 6.49.18 (long-term)
architecture-name: arm
board-name: RB3011UiAS
`);
assert.equal(routerOs6.major, 6);
assert.equal(routerOs6.containerPackage, null);

const legacy = parseRouterFacts(`
version: 7.19.1 (stable)
architecture-name: arm
board-name: RB3011UiAS
0 name="routeros" version="7.19.1"
1 name="container" version="7.19.1" disabled=no
mode: advanced
container: yes
`);
assert.equal(legacy.version, "7.19.1");
assert.equal(legacy.containerPackage, true);
assert.equal(legacy.containerMode, true);
assert.equal(versionAtLeast(legacy, 7, 20, 0), false);

const withoutContainer = parseRouterFacts(`
version: 7.23.1 (stable)
architecture-name: arm64
0 name="routeros" version="7.23.1"
container: no
`);
assert.equal(withoutContainer.containerPackage, false);
assert.equal(withoutContainer.containerMode, false);
assert.equal(versionAtLeast(withoutContainer, 7, 23, 1), true);

assert.equal(isIpv4("192.168.1.1"), true);
assert.equal(isIpv4("192.168.1.256"), false);
assert.equal(isIpv4("1.2.3"), false);
assert.equal(isAsn("64512"), true);
assert.equal(isAsn("0"), false);
assert.equal(isAsn("4294967296"), false);
assert.equal(isStandardCommunity("64512:500"), true);
assert.equal(isStandardCommunity("65536:500"), false);
assert.equal(isStandardCommunity("64512:1:1"), false);

const common = {
  serviceIp: "192.0.2.10",
  serviceAs: "64512",
  routerAs: "65455",
  routerId: "192.0.2.1",
  community: "64512:500",
  customGateway: null,
};

const legacyCommands = buildMikroTikCommands({ ...common, syntax: "legacy-v7" });
assert.match(legacyCommands.prepare, /local\.default-address=192\.0\.2\.1/);
assert.match(legacyCommands.prepare, /as=65455 multihop=yes/);
assert.doesNotMatch(legacyCommands.prepare, /routing\/bgp\/instance/);
assert.match(legacyCommands.prepare, /input\.filter=the333-bgp-quarantine disabled=yes/);

const currentCommands = buildMikroTikCommands({ ...common, syntax: "current-v7", customGateway: "172.18.20.2" });
assert.match(currentCommands.prepare, /routing\/bgp\/instance\/add name=the333-bgp as=65455/);
assert.match(currentCommands.prepare, /instance=the333-bgp/);
assert.match(currentCommands.prepare, /local\.address=192\.0\.2\.1/);
assert.match(currentCommands.prepare, /set gw 172\.18\.20\.2; accept/);
assert.match(currentCommands.activate, /input\.filter=the333-bgp-in disabled=no/);
assert.match(currentCommands.rollback, /input\.filter=the333-bgp-quarantine disabled=yes/);
assert.equal((currentCommands.prepare.match(/the333: reserved/g) ?? []).length, RESERVED_PREFIXES.length * 2);
assert.doesNotMatch(currentCommands.prepare, /PRIVATE|PASSWORD|SECRET/i);

console.log("MikroTik parser and command generator tests passed.");
