import os
import csv
from fft_template_generator import FFTTemplateGenerator

def generate_processors(csv_path, output_base_dir="./generated_cores"):
    """
    Reads the best chromosomes CSV and generates the corresponding Verilog hardware cores.
    """
    if not os.path.exists(output_base_dir):
        os.makedirs(output_base_dir)

    print(f"[*] Reading configurations from {csv_path}...")
    
    with open(csv_path, mode='r') as infile:
        reader = csv.DictReader(infile)
        
        for row in reader:
            # Parse parameters from CSV row
            fft_size = int(float(row['fft_size']))
            solution_id = int(float(row['solution_id']))
            chromosome = row['chromosome'].strip()
            config_type = row['config_type']
            
            print(f"\n[+] Generating {fft_size}-point FFT (ID: {solution_id}, Type: {config_type})")
            print(f"    Chromosome: {chromosome}")
            
            # Instantiate the template generator
            generator = FFTTemplateGenerator(fft_size=fft_size)
            
            # Establish output directory for this specific configuration
            core_dir = os.path.join(output_base_dir, f"fft_{fft_size}_sol_{solution_id}")
            if not os.path.exists(core_dir):
                os.makedirs(core_dir)
                
            # Convert chromosome bit-string to configuration dictionary
            config = generator.chromosome_to_config(chromosome)
            
            # FIX: Call the correct method from FFTTemplateGenerator to actually write the files
            if hasattr(generator, 'generate_complete_fft'):
                generator.generate_complete_fft(chromosome, output_dir=core_dir)
            else:
                print(f"    [!] Error: Generation method 'generate_complete_fft' not found in template generator.")
                continue
                
            print(f"    [✓] Hardware assets saved to: {core_dir}")

if __name__ == "__main__":
    CSV_FILE = "best_chromosomes.csv"
    generate_processors(CSV_FILE)