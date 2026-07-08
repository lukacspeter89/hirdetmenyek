// fetcher — böngésző-TLS (Chrome) letöltő a hirdetmenyek.gov.hu F5 bot-védelme mögé.
// A bogdanfinn/tls-client Chrome TLS + HTTP/2 ujjlenyomatot imitál, így az F5 átengedi.
// Statikusan fordul (CGO nélkül), linux/386-ra is → fut a régi 32-bites Synology NAS-on.
//
// Használat:
//   fetcher <url>
// Kimenet a stdout-ra:
//   első sor:  "HTTP <status>"
//   utána:     a válasz törzse (JSON)
// Kilépési kód: 0 = kaptunk HTTP választ (a státusz a kimenetben); 3 = hálózati hiba.
package main

import (
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
	"time"

	http "github.com/bogdanfinn/fhttp"
	tls_client "github.com/bogdanfinn/tls-client"
	"github.com/bogdanfinn/tls-client/profiles"
)

const (
	baseURL = "https://hirdetmenyek.gov.hu"
)

var idRe = regexp.MustCompile(`/reszletezo/(\d+)`)

func browserHeaders(referer string) http.Header {
	h := http.Header{
		"accept":             {"application/json, text/plain, */*"},
		"accept-language":    {"hu,en-US;q=0.9,en;q=0.8,de;q=0.7"},
		"user-agent":         {"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
		"sec-ch-ua":          {`"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"`},
		"sec-ch-ua-mobile":   {"?0"},
		"sec-ch-ua-platform": {`"Windows"`},
		"sec-fetch-dest":     {"empty"},
		"sec-fetch-mode":     {"cors"},
		"sec-fetch-site":     {"same-origin"},
		http.HeaderOrderKey: {
			"accept", "accept-language", "user-agent", "sec-ch-ua",
			"sec-ch-ua-mobile", "sec-ch-ua-platform", "referer",
			"sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
		},
	}
	if referer != "" {
		h.Set("referer", referer)
	}
	return h
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "használat: fetcher <url>")
		os.Exit(2)
	}
	target := os.Args[1]

	jar := tls_client.NewCookieJar()
	opts := []tls_client.HttpClientOption{
		tls_client.WithTimeoutSeconds(25),
		tls_client.WithClientProfile(profiles.Chrome_131),
		tls_client.WithCookieJar(jar),
	}
	client, err := tls_client.NewHttpClient(tls_client.NewNoopLogger(), opts...)
	if err != nil {
		fmt.Fprintln(os.Stderr, "kliens hiba:", err)
		os.Exit(3)
	}

	// 1) Bemelegítés: főoldal → BIGip terheléselosztó-süti a cookie jar-ba.
	if warm, e := http.NewRequest(http.MethodGet, baseURL+"/", nil); e == nil {
		warm.Header = browserHeaders("")
		warm.Header.Set("sec-fetch-dest", "document")
		warm.Header.Set("sec-fetch-mode", "navigate")
		warm.Header.Set("sec-fetch-site", "none")
		warm.Header.Set("accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
		if wr, e2 := client.Do(warm); e2 == nil {
			io.Copy(io.Discard, wr.Body)
			wr.Body.Close()
		}
		time.Sleep(400 * time.Millisecond)
	}

	// 2) Cél lekérése — Referer az adott hirdetmény oldalára mutat.
	ref := baseURL + "/"
	if m := idRe.FindStringSubmatch(target); m != nil {
		ref = baseURL + "/reszletezo/" + m[1]
	}
	req, err := http.NewRequest(http.MethodGet, target, nil)
	if err != nil {
		fmt.Fprintln(os.Stderr, "kérés hiba:", err)
		os.Exit(3)
	}
	req.Header = browserHeaders(ref)

	resp, err := client.Do(req)
	if err != nil {
		fmt.Fprintln(os.Stderr, "hálózati hiba:", err)
		os.Exit(3)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	fmt.Printf("HTTP %d\n", resp.StatusCode)
	os.Stdout.Write(body)
	if !strings.HasSuffix(string(body), "\n") {
		fmt.Println()
	}
}
