/* Escáner de disco nativo (C++/Win32).

   Hace el mismo recorrido que `pctoolkit\\core\\disk.py` —tamaño de cada carpeta hija,
   recuento de archivos, totales por categoría y los archivos más grandes— pero sin pasar
   por el intérprete: el escaneo de una carpeta grande es el sitio donde Python se nota.

   No decide nada por su cuenta: el catálogo de categorías, los nombres que se saltan, el
   tamaño mínimo de «archivo grande» y el tope de hijos llegan desde Python, y el resultado
   vuelve como JSON (una sola cadena) para no tener que cruzar estructuras entre los dos
   mundos. La librería es **opcional**: si no está compilada, `disk.py` usa su recorrido de
   siempre y todo sigue funcionando.

   Se compila con `nativo\\construir.py` (MSVC o MinGW) y se carga con `ctypes`.

   Detalles que se respetan del recorrido de Python (para que los resultados coincidan):

   * los enlaces simbólicos y las uniones (puntos de reanálisis) **se saltan**, no se siguen;
   * `$recycle.bin`, `System Volume Information` y los temporales de actualización se saltan
     con la lista que manda Python (se compara en minúsculas);
   * `dirs_scanned` cuenta las carpetas que se **abren** (sin contar la raíz) y `errors`
     cuenta las que no se pudieron abrir o medir;
   * los hijos de la raíz se ordenan de mayor a menor y, si hay más del tope, el resto se
     junta en un nodo «Otros N elementos»;

   La cancelación se mira con un entero compartido (`cancelar`), que Python pone a 1 desde su
   hilo cuando el usuario para el análisis. */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cwctype>
#include <map>
#include <string>
#include <vector>

using u64 = unsigned long long;
/* El aviso de progreso que llega desde Python (una función de `ctypes`). */
using Aviso = void(__cdecl*)(void*, const wchar_t*, unsigned long long);

namespace {

struct Nodo {
    std::wstring ruta;
    std::wstring nombre;
    u64 tamano = 0;
    bool es_dir = false;
    u64 archivos = 0;
};

struct ArchivoGrande {
    u64 tamano = 0;
    std::wstring ruta;
    std::wstring nombre;
};

struct Contexto {
    u64 minimo_grande = 0;
    size_t tope_archivos = 0;
    std::vector<std::wstring> saltar;
    std::map<std::wstring, std::wstring> categoria_de_extension;
    std::wstring categoria_otros;
    std::map<std::wstring, u64> categorias;
    std::vector<ArchivoGrande> mayores;
    u64 archivos = 0;
    u64 dirs = 0;
    u64 errores = 0;
    int* cancelar = nullptr;
    ULONGLONG ultimo_aviso = 0;
};

bool cancelado(const Contexto& c) {
    return c.cancelar != nullptr && *c.cancelar != 0;
}

std::string a_utf8(const std::wstring& texto) {
    if (texto.empty()) {
        return std::string();
    }
    int largo = WideCharToMultiByte(CP_UTF8, 0, texto.c_str(), static_cast<int>(texto.size()),
                                    nullptr, 0, nullptr, nullptr);
    if (largo <= 0) {
        return std::string();
    }
    std::string salida(static_cast<size_t>(largo), '\0');
    WideCharToMultiByte(CP_UTF8, 0, texto.c_str(), static_cast<int>(texto.size()), &salida[0],
                        largo, nullptr, nullptr);
    return salida;
}

/* Vuelta a texto ancho de lo que se armó en UTF-8. **No** vale ensanchar byte a byte
   (`std::wstring(json.begin(), json.end())`): eso convierte cada byte acentuado en un carácter
   suelto y «Vídeo» llegaba a Python como «VÃ­deo». Se le pregunta a Windows, que es quien
   sabe de codificaciones. */
std::wstring a_ancho(const std::string& texto) {
    if (texto.empty()) {
        return std::wstring();
    }
    int largo = MultiByteToWideChar(CP_UTF8, 0, texto.c_str(), static_cast<int>(texto.size()),
                                    nullptr, 0);
    if (largo <= 0) {
        return std::wstring();
    }
    std::wstring salida(static_cast<size_t>(largo), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, texto.c_str(), static_cast<int>(texto.size()), &salida[0],
                        largo);
    return salida;
}

/* Comillas de JSON para los nombres: las rutas de Windows llevan barras invertidas, que si no
   se escapan rompen el documento. */
std::string como_json(const std::string& texto) {
    std::string salida;
    salida.reserve(texto.size() + 8);
    salida.push_back('"');
    for (unsigned char letra : texto) {
        switch (letra) {
            case '"':
                salida += "\\\"";
                break;
            case '\\':
                salida += "\\\\";
                break;
            case '\n':
                salida += "\\n";
                break;
            case '\r':
                salida += "\\r";
                break;
            case '\t':
                salida += "\\t";
                break;
            default:
                if (letra < 0x20) {
                    // Control: se escribe como \u00XX sin depender de `sprintf_s`, que no
                    // está en todas las cadenas de compilación.
                    static const char* digitos = "0123456789ABCDEF";
                    salida += "\\u00";
                    salida.push_back(digitos[(letra >> 4) & 0x0F]);
                    salida.push_back(digitos[letra & 0x0F]);
                } else {
                    salida.push_back(static_cast<char>(letra));
                }
        }
    }
    salida.push_back('"');
    return salida;
}

std::wstring minusculas(const std::wstring& texto) {
    std::wstring salida(texto);
    std::transform(salida.begin(), salida.end(), salida.begin(),
                   [](wchar_t letra) { return static_cast<wchar_t>(towlower(letra)); });
    return salida;
}

/* Las rutas largas (más de 260 caracteres) necesitan el prefijo `\\?\` **solo** para las
   llamadas a la API: lo que se devuelve y lo que se enseña es la ruta de siempre. */
std::wstring ruta_para_la_api(const std::wstring& ruta) {
    if (ruta.size() >= 4 && ruta.compare(0, 4, L"\\\\?\\") == 0) {
        return ruta;
    }
    if (ruta.size() >= 2 && ruta[1] == L':') {
        return L"\\\\?\\" + ruta;
    }
    if (ruta.size() >= 2 && ruta[0] == L'\\' && ruta[1] == L'\\') {
        return L"\\\\?\\UNC\\" + ruta.substr(2);
    }
    return ruta;
}

std::wstring unir(const std::wstring& base, const std::wstring& nombre) {
    if (base.empty()) {
        return nombre;
    }
    wchar_t ultimo = base[base.size() - 1];
    if (ultimo == L'\\' || ultimo == L'/') {
        return base + nombre;
    }
    return base + L"\\" + nombre;
}

const std::wstring& categoria_de(const Contexto& c, const std::wstring& nombre) {
    size_t punto = nombre.rfind(L'.');
    if (punto == std::wstring::npos || punto + 1 >= nombre.size()) {
        return c.categoria_otros;
    }
    auto encontrada = c.categoria_de_extension.find(minusculas(nombre.substr(punto)));
    return encontrada == c.categoria_de_extension.end() ? c.categoria_otros : encontrada->second;
}

void guardar_archivo_grande(Contexto& c, u64 tamano, const std::wstring& ruta,
                            const std::wstring& nombre) {
    if (tamano < c.minimo_grande) {
        return;
    }
    c.mayores.push_back({tamano, ruta, nombre});
    if (c.mayores.size() > c.tope_archivos * 2 + 8) {
        std::sort(c.mayores.begin(), c.mayores.end(),
                  [](const ArchivoGrande& a, const ArchivoGrande& b) { return a.tamano > b.tamano; });
        c.mayores.resize(c.tope_archivos);
    }
}

void avisar(Contexto& c, Aviso progress, void* ctx, const std::wstring& ruta) {
    if (progress == nullptr) {
        return;
    }
    ULONGLONG ahora = GetTickCount64();
    if (ahora - c.ultimo_aviso < 80 && c.archivos % 200 != 0) {
        return;
    }
    c.ultimo_aviso = ahora;
    progress(ctx, ruta.c_str(), c.archivos);
}

/* Recorre `ruta` y devuelve (tamaño total, archivos) de todo lo que cuelga de ella. */
void recorrer(Contexto& c, Aviso progress, void* ctx, const std::wstring& ruta, u64& tamano_total,
              u64& archivos_total) {
    tamano_total = 0;
    archivos_total = 0;
    if (cancelado(c)) {
        return;
    }
    WIN32_FIND_DATAW datos;
    std::wstring patron = unir(ruta_para_la_api(ruta), L"*");
    HANDLE busqueda = FindFirstFileExW(patron.c_str(), FindExInfoBasic, &datos, FindExSearchNameMatch,
                                       nullptr, FIND_FIRST_EX_LARGE_FETCH);
    if (busqueda == INVALID_HANDLE_VALUE) {
        c.errores += 1;
        return;
    }
    c.dirs += 1;

    do {
        const std::wstring nombre = datos.cFileName;
        if (nombre == L"." || nombre == L"..") {
            continue;
        }
        if (cancelado(c)) {
            break;
        }
        const bool es_punto_de_reenlace = (datos.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
        if (es_punto_de_reenlace) {
            continue;
        }
        const std::wstring minuscula = minusculas(nombre);
        if (std::find(c.saltar.begin(), c.saltar.end(), minuscula) != c.saltar.end()) {
            continue;
        }
        if ((datos.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            const std::wstring hijo = unir(ruta, nombre);
            u64 tamano_hijo = 0;
            u64 archivos_hijo = 0;
            recorrer(c, progress, ctx, hijo, tamano_hijo, archivos_hijo);
            tamano_total += tamano_hijo;
            archivos_total += archivos_hijo;
        } else {
            const u64 tamano =
                (static_cast<u64>(datos.nFileSizeHigh) << 32) | static_cast<u64>(datos.nFileSizeLow);
            tamano_total += tamano;
            archivos_total += 1;
            c.archivos += 1;
            const std::wstring& categoria = categoria_de(c, nombre);
            c.categorias[categoria] += tamano;
            guardar_archivo_grande(c, tamano, unir(ruta, nombre), nombre);
        }
        avisar(c, progress, ctx, unir(ruta, nombre));
    } while (FindNextFileW(busqueda, &datos) != 0);

    FindClose(busqueda);
}

std::wstring leer_texto(const wchar_t* texto) {
    return texto == nullptr ? std::wstring() : std::wstring(texto);
}

/* Trocea una lista con un separador (los parámetros llegan en una sola cadena). */
std::vector<std::wstring> trocear(const std::wstring& texto, wchar_t separador) {
    std::vector<std::wstring> partes;
    std::wstring actual;
    for (wchar_t letra : texto) {
        if (letra == separador) {
            if (!actual.empty()) {
                partes.push_back(actual);
                actual.clear();
            }
        } else {
            actual.push_back(letra);
        }
    }
    if (!actual.empty()) {
        partes.push_back(actual);
    }
    return partes;
}

std::wstring construir_json(const std::wstring& raiz, const Contexto& c,
                            const std::vector<Nodo>& hijos, size_t tope_hijos) {
    std::string json = "{";
    json += "\"root\":" + como_json(a_utf8(raiz));
    json += ",\"total_size\":" + std::to_string(hijos.empty() ? 0 : [&] {
        u64 total = 0;
        for (const Nodo& nodo : hijos) {
            total += nodo.tamano;
        }
        return total;
    }());
    json += ",\"total_files\":" + std::to_string(c.archivos);
    json += ",\"dirs_scanned\":" + std::to_string(c.dirs);
    json += ",\"errors\":" + std::to_string(c.errores);

    json += ",\"children\":[";
    for (size_t i = 0; i < hijos.size() && i < tope_hijos; ++i) {
        const Nodo& nodo = hijos[i];
        if (i > 0) {
            json += ",";
        }
        json += "{\"path\":" + como_json(a_utf8(nodo.ruta));
        json += ",\"name\":" + como_json(a_utf8(nodo.nombre));
        json += ",\"size\":" + std::to_string(nodo.tamano);
        json += ",\"is_dir\":" + std::string(nodo.es_dir ? "true" : "false");
        json += ",\"file_count\":" + std::to_string(nodo.archivos) + "}";
    }
    json += "]";

    std::vector<std::pair<std::wstring, u64>> categorias(c.categorias.begin(), c.categorias.end());
    std::sort(categorias.begin(), categorias.end(),
              [](const auto& a, const auto& b) { return a.second > b.second; });
    json += ",\"categories\":[";
    for (size_t i = 0; i < categorias.size(); ++i) {
        if (i > 0) {
            json += ",";
        }
        json += "[" + como_json(a_utf8(categorias[i].first)) + "," +
                std::to_string(categorias[i].second) + "]";
    }
    json += "]";

    std::vector<ArchivoGrande> mayores(c.mayores);
    std::sort(mayores.begin(), mayores.end(),
              [](const ArchivoGrande& a, const ArchivoGrande& b) { return a.tamano > b.tamano; });
    if (mayores.size() > c.tope_archivos) {
        mayores.resize(c.tope_archivos);
    }
    json += ",\"largest_files\":[";
    for (size_t i = 0; i < mayores.size(); ++i) {
        if (i > 0) {
            json += ",";
        }
        json += "{\"path\":" + como_json(a_utf8(mayores[i].ruta));
        json += ",\"name\":" + como_json(a_utf8(mayores[i].nombre));
        json += ",\"size\":" + std::to_string(mayores[i].tamano) + "}";
    }
    json += "]}";
    return a_ancho(json);
}

}  // namespace

/* Punto de entrada: devuelve el resultado como JSON (una cadena que libera `pctk_liberar`).

   `saltar` es una lista de nombres separados por saltos de línea (en minúsculas) y
   `categorias` lleva una línea por categoría («Nombre|ext|ext…»). `aviso` se llama de vez en
   cuando con la ruta que se está mirando y `cancelar` se lee a cada paso. */
extern "C" __declspec(dllexport) const wchar_t* pctk_escanear_disco(
    const wchar_t* raiz, unsigned long long minimo_grande, int tope_hijos, int tope_archivos,
    const wchar_t* saltar, const wchar_t* categorias, const wchar_t* categoria_otros,
    Aviso aviso, void* contexto, int* cancelar) {
    Contexto c;
    c.minimo_grande = minimo_grande;
    c.tope_archivos = static_cast<size_t>(tope_archivos < 0 ? 0 : tope_archivos);
    c.saltar = trocear(leer_texto(saltar), L'\n');
    c.categoria_otros = leer_texto(categoria_otros);
    c.cancelar = cancelar;
    for (const std::wstring& linea : trocear(leer_texto(categorias), L'\n')) {
        auto partes = trocear(linea, L'|');
        if (partes.empty()) {
            continue;
        }
        const std::wstring categoria = partes[0];
        for (size_t i = 1; i < partes.size(); ++i) {
            c.categoria_de_extension[minusculas(partes[i])] = categoria;
        }
    }

    const std::wstring raiz_texto = leer_texto(raiz);
    std::vector<Nodo> hijos;
    WIN32_FIND_DATAW datos;
    const std::wstring patron = unir(ruta_para_la_api(raiz_texto), L"*");
    HANDLE busqueda = FindFirstFileExW(patron.c_str(), FindExInfoBasic, &datos, FindExSearchNameMatch,
                                       nullptr, FIND_FIRST_EX_LARGE_FETCH);
    if (busqueda != INVALID_HANDLE_VALUE) {
        do {
            const std::wstring nombre = datos.cFileName;
            if (nombre == L"." || nombre == L"..") {
                continue;
            }
            if (cancelado(c)) {
                break;
            }
            const bool es_punto_de_reenlace =
                (datos.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
            if (es_punto_de_reenlace) {
                continue;
            }
            if (std::find(c.saltar.begin(), c.saltar.end(), minusculas(nombre)) != c.saltar.end()) {
                continue;
            }
            if ((datos.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
                const std::wstring hijo = unir(raiz_texto, nombre);
                u64 tamano = 0;
                u64 archivos = 0;
                recorrer(c, aviso, contexto, hijo, tamano, archivos);
                hijos.push_back({hijo, nombre, tamano, true, archivos});
                avisar(c, aviso, contexto, hijo);
            } else {
                const u64 tamano =
                    (static_cast<u64>(datos.nFileSizeHigh) << 32) | static_cast<u64>(datos.nFileSizeLow);
                c.archivos += 1;
                const std::wstring& categoria = categoria_de(c, nombre);
                c.categorias[categoria] += tamano;
                hijos.push_back({unir(raiz_texto, nombre), nombre, tamano, false, 1});
            }
        } while (FindNextFileW(busqueda, &datos) != 0);
        FindClose(busqueda);
    }

    std::sort(hijos.begin(), hijos.end(), [](const Nodo& a, const Nodo& b) {
        return a.tamano > b.tamano;
    });
    const size_t tope = static_cast<size_t>(tope_hijos < 0 ? 0 : tope_hijos);
    if (hijos.size() > tope) {
        u64 tamano_resto = 0;
        u64 archivos_resto = 0;
        for (size_t i = tope; i < hijos.size(); ++i) {
            tamano_resto += hijos[i].tamano;
            archivos_resto += hijos[i].archivos;
        }
        const size_t cuantos = hijos.size() - tope;
        hijos.resize(tope);
        hijos.push_back({raiz_texto, L"Otros " + std::to_wstring(cuantos) + L" elementos",
                         tamano_resto, false, archivos_resto});
    }

    const std::wstring json = construir_json(raiz_texto, c, hijos, tope + 1);
    wchar_t* salida = static_cast<wchar_t*>(malloc((json.size() + 1) * sizeof(wchar_t)));
    if (salida == nullptr) {
        return nullptr;
    }
    memcpy(salida, json.c_str(), (json.size() + 1) * sizeof(wchar_t));
    return salida;
}

extern "C" __declspec(dllexport) void pctk_liberar(const wchar_t* texto) {
    free(const_cast<wchar_t*>(texto));
}
