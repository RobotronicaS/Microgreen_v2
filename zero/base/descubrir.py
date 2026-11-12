"""Busca cámaras ONVIF en la interfaz Wi-Fi de la Zero."""

import ipaddress
import json
import socket
import subprocess
import time
from urllib.parse import unquote
from uuid import uuid4
from xml.etree import ElementTree


DESCUBRIMIENTO = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
ONVIF = "http://www.onvif.org/ver10/network/wsdl"
PREFIJO_NOMBRE = "onvif://www.onvif.org/name/"
MULTICAST = ("239.255.255.250", 3702)


def direccion_wifi(interfaz):
    try:
        proceso = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "dev", interfaz],
            capture_output=True, text=True, check=True, timeout=3,
        )
        for dispositivo in json.loads(proceso.stdout):
            for direccion in dispositivo["addr_info"]:
                if direccion["scope"] == "global":
                    return ipaddress.IPv4Interface(f"{direccion['local']}/{direccion['prefixlen']}")
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        pass
    raise ValueError(f"{interfaz} no tiene una IPv4 disponible. Revisá el Wi-Fi de la Zero.")


def sonda(tipo):
    return f'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
        xmlns:d="{DESCUBRIMIENTO}" xmlns:dn="{ONVIF}"
        xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <s:Header>
        <a:MessageID>urn:uuid:{uuid4()}</a:MessageID>
        <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
        <a:Action>{DESCUBRIMIENTO}/Probe</a:Action>
      </s:Header>
      <s:Body><d:Probe><d:Types>{tipo}</d:Types></d:Probe></s:Body>
    </s:Envelope>'''.encode("utf-8")


def datos_respuesta(datos):
    if b"\x00" in datos or b"<!DOCTYPE" in datos.upper() or b"<!ENTITY" in datos.upper():
        return None
    try:
        mensaje = ElementTree.fromstring(datos)
    except (ElementTree.ParseError, LookupError):
        return None
    coincidencia = mensaje.find(f".//{{{DESCUBRIMIENTO}}}ProbeMatch")
    if coincidencia is None:
        return None
    etiquetas = coincidencia.findtext(f"{{{DESCUBRIMIENTO}}}Scopes", "").split()
    return next((unquote(x[len(PREFIJO_NOMBRE):])[:80] for x in etiquetas
                 if x.startswith(PREFIJO_NOMBRE)), "Equipo ONVIF")


def buscar_camaras(interfaz="wlan0"):
    red = direccion_wifi(interfaz)
    ip = str(red.ip)
    encontradas = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as canal:
        canal.bind((ip, 0))
        canal.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        canal.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        for tipo in ("dn:NetworkVideoTransmitter", "tds:Device"):
            canal.sendto(sonda(tipo), MULTICAST)
        limite = time.monotonic() + 4
        while len(encontradas) < 64:
            espera = limite - time.monotonic()
            if espera <= 0:
                break
            canal.settimeout(espera)
            try:
                datos, (origen, _) = canal.recvfrom(16384)
            except socket.timeout:
                break
            if origen != ip and ipaddress.IPv4Address(origen) in red.network:
                nombre = datos_respuesta(datos)
                if nombre:
                    encontradas[origen] = nombre

    return {
        "interfaz": interfaz, "ip": ip,
        "camaras": [{"ip": host, "nombre": encontradas[host]}
                    for host in sorted(encontradas, key=ipaddress.IPv4Address)],
    }
