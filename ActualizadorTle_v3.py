import urllib.request
import os
import sys
import subprocess
import time
import shutil
from pathlib import Path

# =====================================================
# CONFIGURACIÓN
# =====================================================

ARCHIVO_CONFIG    = "config.txt"
ARCHIVO_SATELITES = "satelites.txt"

URL_API          = "https://db.satnogs.org/api/tle/?format=3le"
TIMEOUT_DESCARGA = 30   # segundos

LINEAS_POR_SATELITE = 3  # nombre + línea1 TLE + línea2 TLE

ARCHIVO_CACHE  = "tle_cache.txt"

# Valores por defecto
RUTA_SALIDA_DEFECTO   = "mis_satelites.txt"
RUTA_ORBITRON_DEFECTO = r"C:\Radio\Orbitron\Orbitron.exe"
ESPERAR_CIERRE_DEFECTO = False

# =====================================================
# FUNCIONES
# =====================================================

def cargar_configuracion(ruta_config):
    """Carga la configuración desde config.txt"""
    config = {
        'orbitron_path': RUTA_ORBITRON_DEFECTO,
        'tle_output':    RUTA_SALIDA_DEFECTO,
        'wait_for_close': ESPERAR_CIERRE_DEFECTO,
        'horas_cache':   6,  # valor por defecto si no está en config.txt
    }

    if not os.path.exists(ruta_config):
        print(f"⚠ No se encuentra {ruta_config}, usando valores por defecto")
        return config

    try:
        with open(ruta_config, 'r', encoding='utf-8') as archivo:
            for linea in archivo:
                linea = linea.strip()
                if not linea or linea.startswith('#'):
                    continue
                if '=' in linea:
                    clave, valor = linea.split('=', 1)
                    clave = clave.strip().upper()
                    valor = valor.strip()
                    if clave == 'ORBITRON_PATH':
                        config['orbitron_path'] = valor
                    elif clave == 'TLE_OUTPUT':
                        config['tle_output'] = valor
                    elif clave == 'WAIT_FOR_CLOSE':
                        config['wait_for_close'] = valor.lower() == 'true'
                    elif clave == 'HORAS_CACHE':
                        try:
                            config['horas_cache'] = float(valor)
                        except ValueError:
                            print(f"  ⚠ HORAS_CACHE inválido '{valor}', usando {config['horas_cache']}h")

        print(f"✓ Configuración cargada desde {ruta_config}")
        return config

    except Exception as e:
        print(f"✗ Error al leer {ruta_config}: {e}")
        return config


def cargar_satelites(ruta_satelites):
    """Carga la lista de satélites desde satelites.txt.
    Devuelve una lista de tuplas (nombre, norad_id) o None si hay error.
    """
    if not os.path.exists(ruta_satelites):
        print(f"✗ ERROR: No se encuentra el archivo {ruta_satelites}")
        print(f"\n📝 Crea el archivo '{ruta_satelites}' con este formato:")
        print("   ISS (ZARYA),25544")
        print("   FOX-1B (AO-91),43017")
        return None

    satelites = []
    try:
        with open(ruta_satelites, 'r', encoding='utf-8') as archivo:
            for num_linea, linea in enumerate(archivo, 1):
                linea = linea.strip()
                if not linea or linea.startswith('#'):
                    continue
                if ',' not in linea:
                    print(f"  ⚠ Línea {num_linea}: Formato incorrecto (falta la coma)")
                    continue
                partes = linea.split(',', 1)
                nombre = partes[0].strip()
                try:
                    norad = int(partes[1].strip())
                    satelites.append((nombre, norad))
                except ValueError:
                    print(f"  ⚠ Línea {num_linea}: NORAD inválido '{partes[1].strip()}'")

    except Exception as e:
        print(f"✗ Error al leer {ruta_satelites}: {e}")
        return None

    if not satelites:
        print("✗ No se encontraron satélites válidos en el archivo")
        return None

    print(f"✓ Cargados {len(satelites)} satélites desde {ruta_satelites}")
    return satelites


# ---- Caché ---------------------------------------------------------------

def _edad_cache_horas():
    """Devuelve la edad del archivo caché en horas, o None si no existe."""
    if not os.path.exists(ARCHIVO_CACHE):
        return None
    segundos = time.time() - os.path.getmtime(ARCHIVO_CACHE)
    return segundos / 3600


def _leer_cache():
    """Lee y devuelve el contenido del archivo caché."""
    return Path(ARCHIVO_CACHE).read_text(encoding='utf-8')


def _escribir_cache(datos):
    """Escribe los datos descargados en el archivo caché."""
    try:
        Path(ARCHIVO_CACHE).write_text(datos, encoding='utf-8')
    except Exception as e:
        print(f"  ⚠ No se pudo guardar la caché: {e}")


def obtener_datos_tle(horas_cache):
    """Devuelve los datos TLE, usando caché si están frescos.

    Flujo:
      1. Si la caché existe y tiene menos de `horas_cache` horas → la usa.
      2. Si no → descarga de SatNOGS y guarda en caché.
      3. Si la descarga falla pero existe caché (aunque antigua) → la usa como fallback.
    """
    edad = _edad_cache_horas()

    if edad is not None and edad < horas_cache:
        print(f"\n💾 Usando caché local ({edad:.1f}h de antigüedad, límite {horas_cache}h)")
        return _leer_cache()

    if edad is not None:
        print(f"\n💾 Caché caducada ({edad:.1f}h), descargando datos nuevos...")
    else:
        print(f"\n💾 Sin caché local, descargando datos...")

    datos = _descargar_todos_los_tles()

    if datos:
        _escribir_cache(datos)
        return datos

    # Fallback: usar caché antigua si la descarga falló
    if edad is not None:
        print(f"  ⚠ Usando caché antigua como fallback ({edad:.1f}h)")
        return _leer_cache()

    return None


def _descargar_todos_los_tles():
    """Descarga todos los TLEs de SatNOGS y los devuelve como texto."""
    print(f"🌐 Conectando a SatNOGS...")
    try:
        with urllib.request.urlopen(URL_API, timeout=TIMEOUT_DESCARGA) as respuesta:
            datos = respuesta.read().decode('utf-8')
        print(f"✓ Descargados {len(datos):,} bytes de datos")
        return datos
    except Exception as e:
        print(f"✗ Error al descargar: {e}")
        return None


# ---- TLEs ----------------------------------------------------------------

def _epoca_tle(linea1):
    """Extrae la época del TLE como número comparable (año * 1000 + día)."""
    try:
        # La época está en las posiciones [18:32] de la línea 1
        # Formato: AADDD.DDDDDDDD (AA=año, DDD=día del año)
        epoca_str = linea1[18:32].strip()
        anio = int(epoca_str[:2])
        # Años >= 57 son 1900s, el resto 2000s (estándar TLE)
        anio_completo = 1900 + anio if anio >= 57 else 2000 + anio
        dia = float(epoca_str[2:])
        return anio_completo * 1000 + dia
    except (ValueError, IndexError):
        return 0

def parsear_tles(datos_completos):
    tles = {}
    lineas = datos_completos.splitlines()

    for i in range(len(lineas) - 1):
        linea1 = lineas[i]
        if linea1.startswith('1 ') and len(linea1) >= 7:
            norad_str = linea1[2:7].strip()
            linea2 = lineas[i + 1]
            if (linea2.startswith('2 ') and
                len(linea2) >= 7 and
                linea2[2:7].strip() == norad_str):
                # Solo guardar si es más reciente que el que ya tenemos
                if norad_str not in tles or _epoca_tle(linea1) > _epoca_tle(tles[norad_str][0]):
                    tles[norad_str] = (linea1.strip(), linea2.strip())

    print(f"✓ Parseados {len(tles):,} TLEs en memoria")
    return tles

def buscar_tle(tle_dict, norad_id):
    """Busca un satélite por NORAD ID. Devuelve (linea1, linea2) o None."""
    norad_str = str(norad_id).zfill(5)  # NORAD siempre tiene 5 dígitos
    return tle_dict.get(norad_str)


# ---- Backup y guardado ---------------------------------------------------

def hacer_backup(ruta_salida):
    """Copia el archivo de salida actual como .bak antes de sobreescribirlo.
    No hace nada si el archivo aún no existe.
    """
    ruta = Path(ruta_salida)
    if not ruta.exists():
        return  # Primera ejecución, nada que respaldar

    ruta_bak = ruta.with_suffix('.bak' + ruta.suffix)  # mis_satelites.bak.txt
    try:
        shutil.copy2(ruta, ruta_bak)  # copy2 preserva metadatos (fecha de modificación, etc.)
        print(f"💾 Backup creado: {ruta_bak.name}")
    except Exception as e:
        print(f"  ⚠ No se pudo crear el backup: {e}")


def guardar_archivo_salida(lineas_archivo, ruta_salida):
    """Hace backup del archivo anterior y guarda el nuevo con los TLEs."""
    hacer_backup(ruta_salida)
    try:
        Path(ruta_salida).parent.mkdir(parents=True, exist_ok=True)
        with open(ruta_salida, 'w', encoding='utf-8') as archivo:
            for linea in lineas_archivo:
                archivo.write(linea + '\n')
        return True
    except Exception as e:
        print(f"✗ Error al guardar: {e}")
        return False


# ---- Orbitron ------------------------------------------------------------

def lanzar_orbitron(ruta_orbitron, esperar=False):
    """Lanza el programa Orbitron (solo en Windows)."""
    if sys.platform != 'win32':
        print("\n⚠ Orbitron solo está disponible en Windows")
        return False

    if not os.path.exists(ruta_orbitron):
        print(f"\n✗ ERROR: No se encuentra Orbitron en:")
        print(f"   {ruta_orbitron}")
        print(f"   Verifica la ruta en el archivo config.txt")
        return False

    print(f"\n{'=' * 60}")
    print(f"🚀 Lanzando Orbitron...")
    print(f"   {ruta_orbitron}")

    try:
        if esperar:
            subprocess.run([ruta_orbitron], check=True)
            print("✓ Orbitron se ha cerrado")
        else:
            subprocess.Popen([ruta_orbitron])
            print("✓ Orbitron lanzado correctamente")
        return True
    except Exception as e:
        print(f"✗ Error al lanzar Orbitron: {e}")
        return False


# ---- Resumen -------------------------------------------------------------

def mostrar_resumen(satelites_guardados, no_encontrados, ruta_salida):
    """Muestra un resumen de los resultados."""
    print(f"\n{'=' * 60}")
    print(f"📊 RESUMEN FINAL")
    print(f"{'=' * 60}")

    if satelites_guardados:
        print(f"✓ Satélites guardados : {satelites_guardados}")
        print(f"✓ Archivo generado    : {ruta_salida}")

    if no_encontrados:
        print(f"\n⚠ Satélites NO encontrados ({len(no_encontrados)}):")
        for nombre, norad in no_encontrados:
            print(f"    - {nombre} (NORAD: {norad})")
        print(f"\n💡 Sugerencia: Verifica que los números NORAD sean correctos")


# =====================================================
# MAIN
# =====================================================

def main():
    print("=" * 60)
    print("Actualizador de TLEs para Orbitron")
    print("=" * 60)

    # 1. Cargar configuración y lista de satélites
    print(f"\n📁 Leyendo configuración...")
    config = cargar_configuracion(ARCHIVO_CONFIG)

    print(f"\n📁 Leyendo lista de satélites...")
    satelites = cargar_satelites(ARCHIVO_SATELITES)

    if not satelites:
        print("\n✗ No se pudo continuar sin la lista de satélites.")
        print("   Crea el archivo 'satelites.txt' con el formato: NOMBRE,NORAD_ID")
        return

    print(f"\n📋 Configuración:")
    print(f"   - Ruta Orbitron : {config['orbitron_path']}")
    print(f"   - Archivo salida: {config['tle_output']}")
    print(f"   - Esperar cierre: {config['wait_for_close']}")
    print(f"   - Caché (horas) : {config['horas_cache']}")

    # 2. Obtener TLEs (caché o descarga)
    datos = obtener_datos_tle(config['horas_cache'])
    if not datos:
        print("\n⚠ No se pudieron obtener los datos TLE.")
        respuesta = input("¿Lanzar Orbitron igualmente? (s/n): ").strip().lower()
        if respuesta == 's':
            lanzar_orbitron(config['orbitron_path'], config['wait_for_close'])
        return

    tle_dict = parsear_tles(datos)

    # 3. Buscar cada satélite en el diccionario (O(1) por búsqueda)
    print("\n🔍 Buscando TLEs por NORAD ID...")
    lineas_archivo = []
    no_encontrados = []

    for nombre_personalizado, norad in satelites:
        resultado = buscar_tle(tle_dict, norad)
        if resultado:
            linea1, linea2 = resultado
            lineas_archivo.extend([nombre_personalizado, linea1, linea2])
            print(f"  ✓ {nombre_personalizado} → Encontrado")
        else:
            no_encontrados.append((nombre_personalizado, norad))
            print(f"  ✗ {nombre_personalizado} → No encontrado (NORAD: {norad})")

    # 4. Guardar archivo (con backup automático del anterior)
    satelites_guardados = 0
    if lineas_archivo:
        if guardar_archivo_salida(lineas_archivo, config['tle_output']):
            satelites_guardados = len(lineas_archivo) // LINEAS_POR_SATELITE
    else:
        print("\n✗ No se encontró ningún satélite")

    # 5. Mostrar resumen
    mostrar_resumen(satelites_guardados, no_encontrados, config['tle_output'])

    # 6. Lanzar Orbitron
    print("\n" + "-" * 60)
    input("Presiona ENTER para lanzar Orbitron...")
    lanzar_orbitron(config['orbitron_path'], config['wait_for_close'])


if __name__ == "__main__":
    main()