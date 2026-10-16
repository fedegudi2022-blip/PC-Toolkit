/* Listado nativo de paquetes instalados (aplicaciones de la Tienda).

   Lo que hacía `Get-AppxPackage` desde PowerShell —y que costaba unos seis segundos en un
   equipo normal— sale del registro del usuario, que Windows ya tiene al día: los paquetes
   instalados están en

       HKCU\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\
            CurrentVersion\\AppModel\\Repository\\Packages\\<PaqueteCompleto>

   con el nombre visible (`DisplayName`), la carpeta donde está instalado
   (`PackageRootFolder`) y si es un marco de trabajo (`Framework`). De

       HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\
            AppxAllUserStore\\InboxApplications\\<PaqueteCompleto>

   sale qué paquetes vienen con Windows (los que la aplicación marca como «de serie»).

   El nombre visible no siempre está escrito: muchos paquetes lo guardan como una **cadena
   indirecta** (`@{PaqueteCompleto?ms-resource://…}`), que Windows resuelve con
   `SHLoadIndirectString` y es lo que hace este módulo. Lo que no se pueda resolver se deja
   vacío a propósito: así la aplicación enseña el nombre del paquete en vez de un
   `ms-resource:` sin sentido.

   Lo que **no** está aquí: el editor visible (`PublisherDisplayName`) vive en el
   `AppxManifest.xml` de cada paquete, y quien lo lee es Python, que para eso sí tiene
   biblioteca. El nombre, la versión, la familia y el editor (identificador) se deducen del
   identificador del paquete: `Nombre_Versión_Arquitectura_Recurso_Editor`.

   Devuelve un JSON con una lista; la cadena la libera `pctk_liberar`. */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlwapi.h>

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

const wchar_t* CLAVE_PAQUETES =
    L"Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\"
    L"AppModel\\Repository\\Packages";
const wchar_t* CLAVE_DE_SERIE =
    L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\AppxAllUserStore\\InboxApplications";

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

/* Vuelta a texto ancho de lo que se armó en UTF-8 (ver el mismo detalle en
   `escaner_disco.cpp`: ensanchar byte a byte rompe los acentos). */
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

/* Texto de un valor del registro (cadena vacía si no está o es de otro tipo). */
std::wstring leer_texto(HKEY clave, const wchar_t* nombre) {
    DWORD tipo = 0;
    DWORD bytes = 0;
    if (RegQueryValueExW(clave, nombre, nullptr, &tipo, nullptr, &bytes) != ERROR_SUCCESS) {
        return std::wstring();
    }
    if (tipo != REG_SZ && tipo != REG_EXPAND_SZ) {
        return std::wstring();
    }
    std::vector<wchar_t> buffer(bytes / sizeof(wchar_t) + 2, L'\0');
    if (RegQueryValueExW(clave, nombre, nullptr, nullptr,
                         reinterpret_cast<LPBYTE>(buffer.data()), &bytes) != ERROR_SUCCESS) {
        return std::wstring();
    }
    return std::wstring(buffer.data());
}

/* Nombre de verdad de un paquete: si lo guarda como cadena indirecta, se le pregunta a
   Windows (`SHLoadIndirectString`). Lo que no se resuelva se deja vacío en vez de enseñar
   un `@{…}` o un `ms-resource:` sin traducir. */
std::wstring resolver_nombre(const std::wstring& crudo) {
    if (crudo.empty() || (crudo[0] != L'@' && crudo.compare(0, 12, L"ms-resource:") != 0)) {
        return crudo;
    }
    if (crudo[0] != L'@') {
        return std::wstring();
    }
    wchar_t buffer[1024] = {0};
    if (SHLoadIndirectString(crudo.c_str(), buffer, 1024, nullptr) != S_OK) {
        return std::wstring();
    }
    const std::wstring resuelto(buffer);
    if (resuelto.empty() || resuelto[0] == L'@') {
        return std::wstring();
    }
    return resuelto;
}

bool leer_entero(HKEY clave, const wchar_t* nombre) {
    DWORD tipo = 0;
    DWORD valor = 0;
    DWORD bytes = sizeof(valor);
    if (RegQueryValueExW(clave, nombre, nullptr, &tipo, reinterpret_cast<LPBYTE>(&valor), &bytes) !=
        ERROR_SUCCESS) {
        return false;
    }
    return valor != 0;
}

/* Nombres de las subclaves de una clave (vacío si la clave no está). */
std::vector<std::wstring> subclaves(HKEY raiz, const wchar_t* ruta) {
    std::vector<std::wstring> nombres;
    HKEY clave = nullptr;
    if (RegOpenKeyExW(raiz, ruta, 0, KEY_READ, &clave) != ERROR_SUCCESS) {
        return nombres;
    }
    wchar_t nombre[512];
    for (DWORD indice = 0;; ++indice) {
        DWORD largo = 512;
        if (RegEnumKeyExW(clave, indice, nombre, &largo, nullptr, nullptr, nullptr, nullptr) !=
            ERROR_SUCCESS) {
            break;
        }
        nombres.emplace_back(nombre, largo);
    }
    RegCloseKey(clave);
    return nombres;
}

bool es_de_serie(const std::vector<std::wstring>& de_serie, const std::wstring& paquete) {
    for (const std::wstring& nombre : de_serie) {
        if (nombre == paquete) {
            return true;
        }
    }
    return false;
}

}  // namespace

extern "C" __declspec(dllexport) const wchar_t* pctk_listar_paquetes() {
    const std::vector<std::wstring> de_serie = subclaves(HKEY_LOCAL_MACHINE, CLAVE_DE_SERIE);
    std::string json = "[";
    HKEY clave = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, CLAVE_PAQUETES, 0, KEY_READ, &clave) == ERROR_SUCCESS) {
        wchar_t nombre[512];
        bool primero = true;
        for (DWORD indice = 0;; ++indice) {
            DWORD largo = 512;
            if (RegEnumKeyExW(clave, indice, nombre, &largo, nullptr, nullptr, nullptr, nullptr) !=
                ERROR_SUCCESS) {
                break;
            }
            const std::wstring paquete(nombre, largo);
            HKEY detalle = nullptr;
            if (RegOpenKeyExW(clave, paquete.c_str(), 0, KEY_READ, &detalle) != ERROR_SUCCESS) {
                continue;
            }
            const std::wstring visible = resolver_nombre(leer_texto(detalle, L"DisplayName"));
            const std::wstring carpeta = leer_texto(detalle, L"PackageRootFolder");
            const bool marco = leer_entero(detalle, L"Framework");
            RegCloseKey(detalle);

            if (!primero) {
                json += ",";
            }
            primero = false;
            json += "{\"id\":" + como_json(a_utf8(paquete));
            json += ",\"visible\":" + como_json(a_utf8(visible));
            json += ",\"carpeta\":" + como_json(a_utf8(carpeta));
            json += ",\"marco\":" + std::string(marco ? "true" : "false");
            json += ",\"sistema\":" +
                    std::string(es_de_serie(de_serie, paquete) ? "true" : "false");
            json += "}";
        }
        RegCloseKey(clave);
    }
    json += "]";

    const std::wstring texto = a_ancho(json);
    wchar_t* salida = static_cast<wchar_t*>(malloc((texto.size() + 1) * sizeof(wchar_t)));
    if (salida == nullptr) {
        return nullptr;
    }
    memcpy(salida, texto.c_str(), (texto.size() + 1) * sizeof(wchar_t));
    return salida;
}
