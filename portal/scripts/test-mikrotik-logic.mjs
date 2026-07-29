import assert from "node:assert/strict";
import {
  RESERVED_PREFIXES,
  buildMikroTikCommunityFilterCommands,
  buildMikroTikCommands,
  isAsn,
  isIpv4,
  isLargeCommunity,
  isRouterOsObjectName,
  isStandardCommunity,
  isTcpMd5Key,
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
assert.equal(isLargeCommunity("64512:600:1"), true);
assert.equal(isLargeCommunity("4294967296:600:1"), false);
assert.equal(isRouterOsObjectName("the333-bgp-vm"), true);
assert.equal(isRouterOsObjectName('bad" name'), false);
assert.equal(isTcpMd5Key("a-strong_key.1"), true);
assert.equal(isTcpMd5Key("contains spaces"), false);

const common = {
  serviceIp: "192.0.2.10",
  serviceAs: "64512",
  routerAs: "65455",
  routerId: "192.0.2.1",
  community: "64512:500",
  customGateway: null,
  peerMode: "direct",
  ttlSecurityEnabled: false,
  ttlSecurityMin: 255,
  tcpMd5Key: null,
};

const legacyCommands = buildMikroTikCommands({ ...common, syntax: "legacy-v7" });
assert.match(legacyCommands.prepare, /local\.default-address=192\.0\.2\.1/);
assert.match(legacyCommands.prepare, /as=65455 multihop=no/);
assert.doesNotMatch(legacyCommands.prepare, /remote\.ttl=/);
assert.doesNotMatch(legacyCommands.prepare, /routing\/bgp\/instance/);
assert.match(legacyCommands.prepare, /input\.filter=the333-bgp-quarantine disabled=yes/);

const currentCommands = buildMikroTikCommands({ ...common, syntax: "current-v7", customGateway: "172.18.20.2" });
assert.match(currentCommands.prepare, /routing\/bgp\/instance\/add name=the333-bgp as=65455/);
assert.match(currentCommands.prepare, /instance=the333-bgp/);
assert.match(currentCommands.prepare, /local\.address=192\.0\.2\.1/);
assert.match(currentCommands.prepare, /set gw 172\.18\.20\.2; accept/);
assert.match(currentCommands.activate, /input\.filter=the333-bgp-in disabled=no/);
assert.match(currentCommands.activate, /prefix-count/);
assert.doesNotMatch(currentCommands.activate, /routing-protocol=bgp/);
assert.match(currentCommands.rollback, /input\.filter=the333-bgp-quarantine disabled=yes/);
assert.equal((currentCommands.prepare.match(/the333: reserved/g) ?? []).length, RESERVED_PREFIXES.length * 2);
assert.doesNotMatch(currentCommands.prepare, /PRIVATE|PASSWORD|SECRET/i);

const protectedCommands = buildMikroTikCommands({
  ...common,
  syntax: "current-v7",
  peerMode: "direct",
  ttlSecurityEnabled: true,
});
assert.match(protectedCommands.prepare, /multihop=no local\.ttl=255 remote\.ttl=255/);

const multihopCommands = buildMikroTikCommands({
  ...common,
  syntax: "current-v7",
  peerMode: "multihop",
  tcpMd5Key: "test-key_1",
});
assert.match(multihopCommands.prepare, /multihop=yes tcp-md5-key=test-key_1/);
assert.doesNotMatch(multihopCommands.prepare, /remote\.ttl=/);

const profileCommands = buildMikroTikCommunityFilterCommands({
  community: "64512:600:1",
  connectionName: "the333-bgp-vm",
  profileId: "ai-profile",
});
assert.match(profileCommands, /bgp-large-communities includes 64512:600:1/);
assert.match(profileCommands, /BGP connection не найден или имя не уникально/);
assert.match(profileCommands, /name="the333-bgp-vm"\] input\.filter=the333-bgp-profile-in/);
assert.match(profileCommands, /input\.filter=the333-bgp-in/);
assert.match(profileCommands, /prefix-count/);
assert.doesNotMatch(profileCommands, /routing-protocol=bgp/);
assert.equal((profileCommands.match(/the333 profile: reserved/g) ?? []).length, RESERVED_PREFIXES.length);
assert.throws(
  () => buildMikroTikCommunityFilterCommands({ community: "64512:600", connectionName: "the333-bgp", profileId: "bad" }),
  /Large Community/,
);
assert.throws(
  () => buildMikroTikCommunityFilterCommands({ community: "64512:600:1", connectionName: 'bad" name', profileId: "bad" }),
  /connection/,
);

console.log("MikroTik parser and command generator tests passed.");
