/* Listado nativo de paquetes instalados (aplicaciones de la Tienda).

   Lo que hacía `Get-AppxPackage` desde PowerShell —y que costaba unos seis segundos en un
   equipo normal— sale del registro, que Windows ya tiene al día. Se leen **dos** sitios, porque
   ninguno de los dos está completo por sí solo:

   1. Lo que el usuario tiene **registrado**:

          HKCU\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\
               CurrentVersion\\AppModel\\Repository\\Packages\\<PaqueteCompleto>

      con el nombre visible (`DisplayName`), la carpeta donde está instalado
      (`PackageRootFolder`) y si es un marco de trabajo (`Framework`).

   2. Lo que la máquina tiene **puesto para este usuario**:

          HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\
               AppxAllUserStore\\<SID del usuario>\\<PaqueteCompleto>\\Path

      De aquí salen aplicaciones que el repositorio no tiene: en un equipo de escritorio, por
      ejemplo, Intel Arc Software (instalada de verdad, con su carpeta en `WindowsApps`, y que
      `Get-AppxPackage` ni siquiera cuenta). De cada entrada se guarda el `Path`, que apunta al
      manifiesto; la carpeta del paquete es la que lo contiene.

   De las entradas del segundo sitio solo se añaden las que **no son un hueco de paquete** (los
   registros de conjunto llevan `~` entre sus campos: apuntan a `AppxMetadata` y no son una
   aplicación), las que **están en el disco** (muchas son restos de una versión anterior, y
   enseñarlas sería inventar) y las que **no repiten** algo ya listado —ni el paquete exacto ni
   su familia, que es lo que identifica a la aplicación entre versiones y arquitecturas—.

   De

       HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\
            AppxAllUserStore\\InboxApplications\\<PaqueteCompleto>

   sale qué paquetes vienen con Windows (los que la aplicación marca como «de serie»).

   **Lo que el registro no puede dar.** En algunas ediciones —Windows Server— Windows guarda las
   aplicaciones que trae provisionadas (Seguridad de Windows, el Narrador, el Centro de opiniones,
   WSL) **solo en su base de datos interna**, que es la que consulta `Get-AppxPackage`, y no las
   escribe en el registro: comprobado en un Windows Server 2025, donde las cuatro están en el
   disco y en `Get-AppxPackage` pero en ninguna clave del registro. Ahí el listado del registro
   trae menos que el de PowerShell, y no hay forma de arreglarlo leyendo más claves. En Windows
   10 y 11 —los sistemas para los que es la aplicación— pasa lo contrario: el registro trae
   **más** que `Get-AppxPackage`.

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
#include <sddl.h>  // ConvertSidToStringSidW: el SID del usuario, para el almacén de la máquina
#include <shlwapi.h>

#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

const wchar_t* CLAVE_PAQUETES =
    L"Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\"
    L"AppModel\\Repository\\Packages";
const wchar_t* CLAVE_ALMACEN =
    L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\AppxAllUserStore";
const wchar_t* CLAVE_DE_SERIE =
    L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Appx\\AppxAllUserStore\\"
    L"InboxApplications";

/* Una fila del listado. */
struct Paquete {
    std::wstring identificador;
    std::wstring visible;
    std::wstring carpeta;
    bool marco = false;
    bool sistema = false;
};

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
std::vector<std::wstring> subclaves(HKEY raiz, const std::wstring& ruta) {
    std::vector<std::wstring> nombres;
    HKEY clave = nullptr;
    if (RegOpenKeyExW(raiz, ruta.c_str(), 0, KEY_READ, &clave) != ERROR_SUCCESS) {
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

/* Los nombres de paquete de Windows no distinguen mayúsculas, y de hecho el mismo paquete
   aparece escrito de las dos maneras según la clave (`…AppRep…` en una y `…Apprep…` en otra):
   comparar tal cual daría por nuevos paquetes que ya están y por «de fuera» los de serie. */
bool igual_sin_mayusculas(const std::wstring& uno, const std::wstring& otro) {
    if (uno.size() != otro.size()) {
        return false;
    }
    return CompareStringOrdinal(uno.c_str(), static_cast<int>(uno.size()), otro.c_str(),
                                static_cast<int>(otro.size()), TRUE) == CSTR_EQUAL;
}

bool es_de_serie(const std::vector<std::wstring>& de_serie, const std::wstring& paquete) {
    for (const std::wstring& nombre : de_serie) {
        if (igual_sin_mayusculas(nombre, paquete)) {
            return true;
        }
    }
    return false;
}

/* Campos del identificador de un paquete: `Nombre_Versión_Arquitectura_Recurso_Editor`. */
std::vector<std::wstring> campos(const std::wstring& identificador) {
    std::vector<std::wstring> partes;
    size_t inicio = 0;
    while (inicio <= identificador.size()) {
        const size_t corte = identificador.find(L'_', inicio);
        if (corte == std::wstring::npos) {
            partes.push_back(identificador.substr(inicio));
            break;
        }
        partes.push_back(identificador.substr(inicio, corte - inicio));
        inicio = corte + 1;
    }
    return partes;
}

/* Familia del paquete: nombre y editor, que es lo que identifica a la aplicación entre
   versiones y arquitecturas (`Microsoft.BingNews_8wekyb3d8bbwe`). */
std::wstring familia_del_paquete(const std::wstring& identificador) {
    const std::vector<std::wstring> partes = campos(identificador);
    if (partes.size() < 2) {
        return identificador;
    }
    return partes.front() + L"_" + partes.back();
}

/* ¿Es un hueco de paquete? Los registros de conjunto llevan `~` en vez de la arquitectura o
   del recurso: apuntan a `AppxMetadata` y no son una aplicación instalada. */
bool es_hueco(const std::wstring& identificador) {
    for (const std::wstring& parte : campos(identificador)) {
        if (parte == L"~") {
            return true;
        }
    }
    return false;
}

std::wstring sin_ultimo_componente(const std::wstring& ruta) {
    const size_t corte = ruta.find_last_of(L"\\/");
    return corte == std::wstring::npos ? std::wstring() : ruta.substr(0, corte);
}

std::wstring ultimo_componente(const std::wstring& ruta) {
    const size_t corte = ruta.find_last_of(L"\\/");
    return corte == std::wstring::npos ? ruta : ruta.substr(corte + 1);
}

bool existe_la_carpeta(const std::wstring& ruta) {
    if (ruta.empty()) {
        return false;
    }
    const DWORD atributos = GetFileAttributesW(ruta.c_str());
    return atributos != INVALID_FILE_ATTRIBUTES && (atributos & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

/* Carpeta del paquete según el `Path` del almacén, que apunta a su manifiesto. En los
   conjuntos el manifiesto está un nivel más abajo (`AppxMetadata`). */
std::wstring carpeta_del_paquete(HKEY almacen, const std::wstring& paquete) {
    HKEY detalle = nullptr;
    if (RegOpenKeyExW(almacen, paquete.c_str(), 0, KEY_READ, &detalle) != ERROR_SUCCESS) {
        return std::wstring();
    }
    const std::wstring ruta = leer_texto(detalle, L"Path");
    RegCloseKey(detalle);
    if (ruta.empty()) {
        return std::wstring();
    }
    std::wstring carpeta = sin_ultimo_componente(ruta);
    if (ultimo_componente(carpeta) == L"AppxMetadata") {
        carpeta = sin_ultimo_componente(carpeta);
    }
    return carpeta;
}

/* SID del usuario que ejecuta esto, para saber qué tiene puesto la máquina para él. */
std::wstring sid_del_usuario() {
    HANDLE testigo = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &testigo)) {
        return std::wstring();
    }
    DWORD largo = 0;
    GetTokenInformation(testigo, TokenUser, nullptr, 0, &largo);
    if (largo == 0) {
        CloseHandle(testigo);
        return std::wstring();
    }
    std::vector<unsigned char> datos(largo, 0);
    std::wstring sid;
    if (GetTokenInformation(testigo, TokenUser, datos.data(), largo, &largo)) {
        TOKEN_USER* usuario = reinterpret_cast<TOKEN_USER*>(datos.data());
        LPWSTR texto = nullptr;
        if (ConvertSidToStringSidW(usuario->User.Sid, &texto)) {
            sid.assign(texto);
            LocalFree(texto);
        }
    }
    CloseHandle(testigo);
    return sid;
}

/* Paquetes del repositorio del usuario: los que tiene registrados. */
std::vector<Paquete> del_repositorio(const std::vector<std::wstring>& de_serie) {
    std::vector<Paquete> paquetes;
    HKEY clave = nullptr;
    if (RegOpenKeyExW(HKEY_CURRENT_USER, CLAVE_PAQUETES, 0, KEY_READ, &clave) != ERROR_SUCCESS) {
        return paquetes;
    }
    wchar_t nombre[512];
    for (DWORD indice = 0;; ++indice) {
        DWORD largo = 512;
        if (RegEnumKeyExW(clave, indice, nombre, &largo, nullptr, nullptr, nullptr, nullptr) !=
            ERROR_SUCCESS) {
            break;
        }
        const std::wstring paquete(nombre, largo);
        Paquete fila;
        fila.identificador = paquete;
        fila.sistema = es_de_serie(de_serie, paquete);
        // Si la clave del paquete no se puede abrir, el paquete se lista igual con lo que ya se
        // sabe (su nombre): dejarlo fuera sería perder una aplicación por no poder leer un
        // detalle, que es justo lo que no tiene que pasar.
        HKEY detalle = nullptr;
        if (RegOpenKeyExW(clave, paquete.c_str(), 0, KEY_READ, &detalle) == ERROR_SUCCESS) {
            fila.visible = resolver_nombre(leer_texto(detalle, L"DisplayName"));
            fila.carpeta = leer_texto(detalle, L"PackageRootFolder");
            fila.marco = leer_entero(detalle, L"Framework");
            RegCloseKey(detalle);
        }
        paquetes.push_back(fila);
    }
    RegCloseKey(clave);
    return paquetes;
}

bool ya_esta(const std::vector<Paquete>& paquetes, const std::wstring& paquete) {
    const std::wstring familia = familia_del_paquete(paquete);
    for (const Paquete& fila : paquetes) {
        if (igual_sin_mayusculas(fila.identificador, paquete) ||
            igual_sin_mayusculas(familia_del_paquete(fila.identificador), familia)) {
            return true;
        }
    }
    return false;
}

/* Lo que añade el almacén de la máquina para este usuario: aplicaciones que el repositorio no
   tiene, con carpeta de verdad en el disco. */
void anadir_del_almacen(std::vector<Paquete>& paquetes, const std::wstring& sid) {
    if (sid.empty()) {
        return;
    }
    const std::wstring ruta = std::wstring(CLAVE_ALMACEN) + L"\\" + sid;
    HKEY clave = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, ruta.c_str(), 0, KEY_READ, &clave) != ERROR_SUCCESS) {
        return;
    }
    for (const std::wstring& paquete : subclaves(HKEY_LOCAL_MACHINE, ruta)) {
        if (es_hueco(paquete) || ya_esta(paquetes, paquete)) {
            continue;
        }
        const std::wstring carpeta = carpeta_del_paquete(clave, paquete);
        if (!existe_la_carpeta(carpeta)) {
            continue;
        }
        Paquete fila;
        fila.identificador = paquete;
        // El almacén no guarda el nombre visible; se deja vacío y la aplicación enseña el
        // nombre del paquete, como hace con los que no se pueden resolver.
        fila.visible = std::wstring();
        fila.carpeta = carpeta;
        fila.marco = false;
        fila.sistema = false;
        paquetes.push_back(fila);
    }
    RegCloseKey(clave);
}

std::string como_json(const std::vector<Paquete>& paquetes) {
    std::string json = "[";
    bool primero = true;
    for (const Paquete& fila : paquetes) {
        if (!primero) {
            json += ",";
        }
        primero = false;
        json += "{\"id\":" + como_json(a_utf8(fila.identificador));
        json += ",\"visible\":" + como_json(a_utf8(fila.visible));
        json += ",\"carpeta\":" + como_json(a_utf8(fila.carpeta));
        json += ",\"marco\":" + std::string(fila.marco ? "true" : "false");
        json += ",\"sistema\":" + std::string(fila.sistema ? "true" : "false");
        json += "}";
    }
    json += "]";
    return json;
}

}  // namespace

extern "C" __declspec(dllexport) const wchar_t* pctk_listar_paquetes() {
    const std::vector<std::wstring> de_serie = subclaves(HKEY_LOCAL_MACHINE, CLAVE_DE_SERIE);
    std::vector<Paquete> paquetes = del_repositorio(de_serie);
    anadir_del_almacen(paquetes, sid_del_usuario());

    const std::wstring texto = a_ancho(como_json(paquetes));
    wchar_t* salida = static_cast<wchar_t*>(malloc((texto.size() + 1) * sizeof(wchar_t)));
    if (salida == nullptr) {
        return nullptr;
    }
    memcpy(salida, texto.c_str(), (texto.size() + 1) * sizeof(wchar_t));
    return salida;
}
