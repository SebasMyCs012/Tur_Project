from pathlib import Path
from datetime import datetime
import time

import pandas as pd
from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURACIÓN
# ============================================================

URL_SISTEMA = (
    "https://prd.impact.spsfi.broadridge.com/"
    "impact_hsbpro/controller"
)

PERFIL = (Path.cwd() / "perfil_chrome_sistema").resolve()

COLUMNAS = [
    "Entity",
    "External Trade ID",
    "Trader",
    "Cusip",
    "Name",
    "Date",
    "Status",
    "Security Id",
    "Amount_External",
    "Depository",
    "Amount_Normalized",
    "Cur",
]

ERRORES_OBJETIVO = {
    "SECURITY NOT FOUND",
    "SECURITY DOES NOT EXIST IN MSD, BROADCAST SENT",
}

MENSAJE_FIN = "LAST QUALIFYING TRANSACTION ALREADY SHOWN"

MAX_PAGINAS = 100


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def limpiar_texto(texto):
    return " ".join(texto.split()).strip()


def texto_visible(page):
    return page.locator("body").inner_text(timeout=30000)


def obtener_tabla_objetivo(page):
    """
    Busca la tabla que contiene 'External Trade ID'.
    """

    tablas = page.locator("table")

    for i in range(tablas.count()):
        try:
            tabla = tablas.nth(i)
            contenido = limpiar_texto(tabla.inner_text())

            if "External Trade ID" in contenido:
                return tabla, i

        except Exception:
            continue

    return None, None


def tabla_visible(page):
    """
    Comprueba si la tabla principal está visible.
    """

    tabla, _ = obtener_tabla_objetivo(page)
    return tabla is not None


def extraer_pagina(page, numero_pagina):
    """
    Extrae las filas de la tabla de la página actual.

    Devuelve:
        registros: lista de diccionarios.
        firma: texto que identifica el contenido de la página.
    """

    tabla, indice_tabla = obtener_tabla_objetivo(page)

    if tabla is None:
        print(
            f"[Página {numero_pagina}] "
            "No se encontró la tabla objetivo.",
            flush=True,
        )
        return [], ""

    print(
        f"[Página {numero_pagina}] "
        f"Tabla objetivo encontrada en índice {indice_tabla}.",
        flush=True,
    )

    filas = tabla.locator("tr")
    registros = []

    for i in range(filas.count()):
        fila = filas.nth(i)
        celdas = fila.locator("th, td")
        valores = []

        for j in range(celdas.count()):
            try:
                valor = limpiar_texto(celdas.nth(j).inner_text())
            except Exception:
                valor = ""

            valores.append(valor)

        # Ignorar encabezados y filas auxiliares
        if len(valores) != len(COLUMNAS):
            continue

        if valores[0] == "Entity":
            continue

        registro = dict(zip(COLUMNAS, valores))
        registro["Pagina"] = numero_pagina
        registros.append(registro)

    firma = "|".join(
        f"{registro['External Trade ID']}::"
        f"{registro['Status']}::"
        f"{registro['Cusip']}"
        for registro in registros
    )

    print(
        f"[Página {numero_pagina}] "
        f"Filas extraídas: {len(registros)}",
        flush=True,
    )

    return registros, firma


def obtener_enlace_next(page):
    """
    Busca el enlace cuyo texto visible sea Next.
    """

    enlaces = page.locator("a")

    for i in range(enlaces.count()):
        enlace = enlaces.nth(i)

        try:
            texto = limpiar_texto(enlace.inner_text())

            if texto.upper() == "NEXT":
                return enlace

        except Exception:
            continue

    return None


def guardar_captura(page, carpeta, nombre_archivo):
    """
    Guarda una captura completa de la página.
    """

    ruta = carpeta / nombre_archivo

    page.screenshot(
        path=str(ruta),
        full_page=True,
    )

    print(
        f"Captura guardada: {ruta.name}",
        flush=True,
    )

    return ruta


def esperar_cambio_de_pagina(
    page,
    firma_anterior,
    segundos_maximos=30,
):
    """
    Espera a que la tabla cambie después de pulsar Next.

    Devuelve:
        CAMBIO     si la tabla cambió.
        FIN        si apareció el mensaje de última página.
        SIN_CAMBIO si agotó el tiempo.
    """

    inicio = time.time()

    while time.time() - inicio < segundos_maximos:
        time.sleep(0.5)

        try:
            texto_actual = texto_visible(page).upper()

            if MENSAJE_FIN in texto_actual:
                return "FIN"

            _, firma_actual = extraer_pagina(page, -1)

            if firma_actual and firma_actual != firma_anterior:
                return "CAMBIO"

        except Exception:
            pass

    return "SIN_CAMBIO"


def deduplicar_registros(df):
    """
    Elimina duplicados cuando existe External Trade ID.

    Las filas sin External Trade ID se conservan.
    """

    columna_id = "External Trade ID"

    con_id = df[
        df[columna_id]
        .fillna("")
        .astype(str)
        .str.strip()
        != ""
    ].copy()

    sin_id = df[
        df[columna_id]
        .fillna("")
        .astype(str)
        .str.strip()
        == ""
    ].copy()

    con_id = con_id.drop_duplicates(
        subset=[columna_id],
        keep="first",
    )

    return pd.concat(
        [con_id, sin_id],
        ignore_index=True,
    )


# ============================================================
# EXTRACCIÓN COMPLETA DE UNA EJECUCIÓN
# ============================================================

def ejecutar_extraccion(page):
    """
    Procesa todas las páginas desde la posición actual
    y genera una carpeta de evidencia independiente.
    """

    fecha_ejecucion = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    evidencia = (
        Path.cwd() / f"TUR Evidence_{fecha_ejecucion}"
    )

    evidencia.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print("=" * 60)
    print("INICIANDO NUEVA EXTRACCIÓN")
    print("=" * 60)
    print(f"Carpeta de evidencia: {evidencia}")
    print()

    if not tabla_visible(page):
        print(
            "No se detectó la tabla con 'External Trade ID'."
        )
        print(
            "Ve manualmente a la primera página de la tabla "
            "y vuelve a intentarlo."
        )
        return False

    registros_totales = []
    firmas_vistas = set()
    paginas_procesadas = 0

    for numero_pagina in range(1, MAX_PAGINAS + 1):
        print()
        print("-" * 60)
        print(f"Procesando página {numero_pagina}")
        print("-" * 60)

        # Tiempo para que termine de renderizarse la tabla
        page.wait_for_timeout(2000)

        registros, firma_actual = extraer_pagina(
            page,
            numero_pagina,
        )

        if not registros:
            print(
                "No se encontraron registros. "
                "Se detiene esta extracción."
            )
            break

        if firma_actual in firmas_vistas:
            print(
                "La página ya se había procesado. "
                "Se detiene para evitar duplicados."
            )
            break

        firmas_vistas.add(firma_actual)
        registros_totales.extend(registros)
        paginas_procesadas = numero_pagina

        # Captura de la página actual
        guardar_captura(
            page,
            evidencia,
            f"TUR_page_{numero_pagina:03d}.png",
        )

        texto_actual = texto_visible(page).upper()

        # Si el aviso ya aparece, se captura y termina
        if MENSAJE_FIN in texto_actual:
            print(
                "Se detectó el aviso de última página."
            )

            guardar_captura(
                page,
                evidencia,
                f"TUR_last_page_notice_{numero_pagina:03d}.png",
            )

            break

        enlace_next = obtener_enlace_next(page)

        if enlace_next is None:
            print(
                "No se encontró el enlace Next. "
                "Se detiene esta extracción."
            )
            break

        firma_anterior = firma_actual

        print("Pulsando Next...", flush=True)

        try:
            enlace_next.click(timeout=30000)

        except Exception as error:
            print(
                f"No se pudo pulsar Next: {error}"
            )
            break

        resultado = esperar_cambio_de_pagina(
            page,
            firma_anterior,
            segundos_maximos=30,
        )

        if resultado == "FIN":
            print(
                "Se detectó: "
                "Last Qualifying Transaction Already Shown"
            )

            guardar_captura(
                page,
                evidencia,
                f"TUR_last_page_notice_{numero_pagina:03d}.png",
            )

            break

        if resultado == "SIN_CAMBIO":
            print(
                "La tabla no cambió después de pulsar Next. "
                "Se detiene por seguridad."
            )
            break

        print(
            "La nueva página se cargó correctamente.",
            flush=True,
        )

    if not registros_totales:
        print(
            "No se extrajeron registros en esta ejecución."
        )
        return False

    # ========================================================
    # GENERAR EXCEL
    # ========================================================

    df = pd.DataFrame(
        registros_totales,
        columns=COLUMNAS + ["Pagina"],
    )

    df = deduplicar_registros(df)

    df["Status_normalizado"] = (
        df["Status"]
        .fillna("")
        .astype(str)
        .str.upper()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    df_filtrado = df[
        df["Status_normalizado"].isin(ERRORES_OBJETIVO)
    ].copy()

    df.drop(
        columns=["Status_normalizado"],
        inplace=True,
    )

    df_filtrado.drop(
        columns=["Status_normalizado"],
        inplace=True,
    )

    archivo_todos = (
        evidencia / "TUR_requests_todas_paginas.xlsx"
    )

    archivo_filtrado = (
        evidencia / "TUR_requests_filtrados.xlsx"
    )

    df.to_excel(
        archivo_todos,
        index=False,
    )

    df_filtrado.to_excel(
        archivo_filtrado,
        index=False,
    )

    # ========================================================
    # RESUMEN
    # ========================================================

    print()
    print("=" * 60)
    print("EXTRACCIÓN TERMINADA")
    print("=" * 60)
    print(f"Páginas procesadas: {paginas_procesadas}")
    print(f"Filas totales: {len(df)}")
    print(f"Filas filtradas: {len(df_filtrado)}")
    print()
    print(f"Carpeta de evidencia:")
    print(evidencia)
    print()
    print(f"Excel completo:")
    print(archivo_todos)
    print()
    print(f"Excel filtrado:")
    print(archivo_filtrado)
    print()

    print("Estados encontrados en el Excel filtrado:")

    if len(df_filtrado) > 0:
        print(
            df_filtrado["Status"]
            .value_counts()
            .to_string()
        )
    else:
        print("Ninguno")

    return True


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

with sync_playwright() as p:
    print("Abriendo Chrome y el sistema...", flush=True)

    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PERFIL),
        channel="chrome",
        headless=False,
        timeout=30000,
    )

    page = context.pages[0] if context.pages else context.new_page()

    page.goto(
        URL_SISTEMA,
        wait_until="commit",
        timeout=60000,
    )

    print()
    print("1. Introduce tus credenciales manualmente en Chrome.")
    print("2. Completa MFA si el sistema lo solicita.")
    print("3. Navega hasta la primera página de la tabla.")
    print("4. Asegúrate de que la tabla esté visible.")
    print("5. Regresa a esta consola y pulsa Enter.")
    input()

    if not tabla_visible(page):
        print()
        print(
            "No se detectó la tabla con "
            "'External Trade ID'."
        )
        print(
            "Comprueba que estás en la pantalla correcta."
        )

        respuesta = input(
            "¿Quieres continuar de todos modos? (s/n): "
        ).strip().lower()

        if respuesta != "s":
            print("Proceso cancelado.")
            context.close()
            raise SystemExit(0)

    while True:
        ejecutar_extraccion(page)

        print()
        print("=" * 60)
        print("¿QUÉ QUIERES HACER AHORA?")
        print("=" * 60)
        print("1 - Repetir la extracción")
        print("2 - Dejar Chrome abierto y terminar")
        print("3 - Cerrar Chrome y terminar")

        opcion = input(
            "Selecciona 1, 2 o 3: "
        ).strip()

        if opcion == "1":
            print()
            print(
                "Deja Chrome en la primera página de la tabla."
            )
            print(
                "Cuando esté listo, vuelve aquí y pulsa Enter."
            )
            input()

            if not tabla_visible(page):
                print()
                print(
                    "No se detectó la tabla en la pestaña "
                    "controlada por Playwright."
                )

                continuar = input(
                    "¿Quieres volver a comprobarla? (s/n): "
                ).strip().lower()

                if continuar != "s":
                    print(
                        "No se inició la nueva extracción."
                    )
                    continue

            continue

        if opcion == "2":
            print()
            print(
                "El script terminará y se intentará dejar "
                "Chrome abierto."
            )
            print(
                "La sesión y la ventana quedarán disponibles "
                "para continuar manualmente."
            )

            # No se llama a context.close().
            # Esto evita cerrar Chrome explícitamente.
            break

        if opcion == "3":
            print()
            print("Cerrando Chrome...")
            context.close()
            break

        print()
        print("Opción no válida. Selecciona 1, 2 o 3.")
        